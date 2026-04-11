#!/usr/bin/env python3
"""
bee_system.py — Raspberry Pi Motion-Reactive Video Kiosk
---------------------------------------------------------
Idle state   : loops idle.mp4 continuously
Motion event : randomly plays react_1.mp4 or react_2.mp4, then returns to idle
Debug mode   : enabled via --debug flag OR press 'D' at runtime to toggle
Fullscreen   : default on, suppressed in debug mode

Usage:
    python3 bee_system.py           # Kiosk / production mode
    python3 bee_system.py --debug   # Debug mode with CV2 windows and overlays
"""

import cv2
import vlc
import time
import threading
import random
import argparse
import sys
import os
from pathlib import Path

# ── Force display environment for SSH + local HDMI use ──────────────────────
os.environ.setdefault("DISPLAY", ":0")
os.environ.setdefault("XAUTHORITY", "/home/beedisplay/.Xauthority")

# ─────────────────────────────────────────────
# CONFIG — edit paths and tuning values here
# ─────────────────────────────────────────────

VIDEO_IDLE    = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/idle.mp4")
VIDEO_REACT_1 = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/react_1.mp4")
VIDEO_REACT_2 = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/react_2.mp4")

CAMERA_DEVICE     = "/dev/video0"
CAPTURE_WIDTH     = 640
CAPTURE_HEIGHT    = 480
DETECT_SCALE      = 0.4       # Downscale factor for motion detection (perf)
MOG2_HISTORY      = 500
MOG2_THRESHOLD    = 50
MIN_AREA          = 1500      # Minimum contour area to count as real motion
MOTION_COOLDOWN   = 3.0       # Seconds to ignore motion after a reaction starts
FRAME_STALE_LIMIT = 3.0       # Seconds before camera is considered stalled
MAIN_LOOP_SLEEP   = 0.033     # ~30 Hz main loop target

# Zone boundaries (as fraction of frame width)
ZONE_LEFT_MAX  = 0.33
ZONE_RIGHT_MIN = 0.67

# ─────────────────────────────────────────────
# ARGUMENT PARSING
# ─────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Bee Motion Video System")
parser.add_argument(
    "--debug", action="store_true",
    help="Enable debug mode: shows CV2 windows and motion overlays"
)
args = parser.parse_args()

# ─────────────────────────────────────────────
# VALIDATE VIDEO FILES
# ─────────────────────────────────────────────

for vpath in [VIDEO_IDLE, VIDEO_REACT_1, VIDEO_REACT_2]:
    if not vpath.exists():
        print(f"[ERROR] Video file not found: {vpath}")
        sys.exit(1)

# ─────────────────────────────────────────────
# FRAME GRABBER — threaded camera capture
# ─────────────────────────────────────────────

class FrameGrabber:
    """
    Runs camera capture in a background thread.
    Main loop calls get_latest_frame() — never blocks on cap.read().
    """
    def __init__(self, device, width, height):
        self._cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self._cap.isOpened():
            raise RuntimeError(f"[FrameGrabber] Cannot open camera: {device}")

        self._frame     = None
        self._lock      = threading.Lock()
        self._last_time = time.time()
        self._running   = True
        self._thread    = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[FrameGrabber] Camera thread started.")

    def _run(self):
        while self._running:
            ret, frame = self._cap.read()
            if ret and frame is not None:
                with self._lock:
                    self._frame     = frame
                    self._last_time = time.time()

    def get_latest_frame(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def age(self):
        """Returns seconds since last successful frame grab."""
        with self._lock:
            return time.time() - self._last_time

    def stop(self):
        self._running = False
        self._thread.join(timeout=2.0)
        self._cap.release()
        print("[FrameGrabber] Camera released.")


# ─────────────────────────────────────────────
# BEE PLAYER — VLC state machine
# ─────────────────────────────────────────────

class BeePlayer:
    """
    Two-state VLC controller:
      IDLE      -> loops idle.mp4
      REACTING  -> plays a reaction video once, then returns to IDLE
    """
    STATE_IDLE     = "IDLE"
    STATE_REACTING = "REACTING"

    def __init__(self, fullscreen: bool):
        vlc_args = [
            "--no-video-title-show",
            "--quiet",
            "--no-xlib",                    # prevents X threading conflicts on Pi
        ]
        if fullscreen:
            vlc_args += [
                "--fullscreen",
                "--video-on-top",           # forces VLC window to front of display
            ]
        else:
            vlc_args += [
                "--no-fullscreen",
                "--width=800",
                "--height=600",
                "--video-on-top",           # keeps VLC visible next to CV2 debug window
            ]

        self._instance   = vlc.Instance(" ".join(vlc_args))
        self._player     = self._instance.media_player_new()
        self._state      = self.STATE_IDLE
        self._fullscreen = fullscreen

        if fullscreen:
            self._player.set_fullscreen(True)

        self._play_idle()

    def _make_media(self, path: Path):
        return self._instance.media_new(str(path))

    def _play_idle(self):
        media = self._make_media(VIDEO_IDLE)
        media.add_option("input-repeat=65535")   # loop effectively forever
        self._player.set_media(media)
        self._player.play()
        self._state = self.STATE_IDLE
        print("[BeePlayer] -> IDLE loop")

    def trigger_reaction(self):
        """Called when motion is detected. Picks a random reaction video."""
        chosen = random.choice([VIDEO_REACT_1, VIDEO_REACT_2])
        media  = self._make_media(chosen)
        self._player.set_media(media)
        self._player.play()
        self._state = self.STATE_REACTING
        print(f"[BeePlayer] -> REACTING  ({chosen.name})")

    def poll(self):
        """
        Called every main loop cycle.
        Detects end-of-reaction and transitions back to idle.
        """
        if self._state == self.STATE_REACTING:
            state = self._player.get_state()
            if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
                print("[BeePlayer] Reaction ended -> returning to IDLE")
                self._play_idle()

    @property
    def is_idle(self):
        return self._state == self.STATE_IDLE

    def stop(self):
        self._player.stop()
        print("[BeePlayer] Stopped.")


# ─────────────────────────────────────────────
# MOTION DETECTOR
# ─────────────────────────────────────────────

class MotionDetector:
    """
    MOG2-based motion detector.
    Returns (motion_detected: bool, zone: str, debug_frame)
    zone is one of: 'left', 'centre', 'right', or None
    """
    def __init__(self):
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=MOG2_HISTORY,
            varThreshold=MOG2_THRESHOLD,
            detectShadows=False
        )

    def process(self, frame, debug_mode: bool):
        h, w    = frame.shape[:2]
        dw      = int(w * DETECT_SCALE)
        dh      = int(h * DETECT_SCALE)
        small   = cv2.resize(frame, (dw, dh))

        mask    = self._bg.apply(small)
        mask    = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))

        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        motion_detected = False
        zone            = None
        debug_frame     = frame.copy() if debug_mode else None

        for cnt in cnts:
            area = cv2.contourArea(cnt)
            if area < MIN_AREA:
                continue

            motion_detected = True
            M = cv2.moments(cnt)
            if M["m00"] > 0:
                cx      = M["m10"] / M["m00"]
                cx_norm = cx / dw   # normalised 0.0 to 1.0

                if cx_norm < ZONE_LEFT_MAX:
                    zone = "left"
                elif cx_norm > ZONE_RIGHT_MIN:
                    zone = "right"
                else:
                    zone = "centre"

            # Scale contour back to full resolution for overlay
            if debug_mode and debug_frame is not None:
                scale_x    = w / dw
                scale_y    = h / dh
                cnt_scaled = (cnt * [scale_x, scale_y]).astype(int)
                cv2.drawContours(debug_frame, [cnt_scaled], -1, (0, 255, 0), 2)

        if debug_mode and debug_frame is not None:
            # Draw zone divider lines on full-res frame
            cv2.line(debug_frame,
                     (int(w * ZONE_LEFT_MAX), 0),
                     (int(w * ZONE_LEFT_MAX), h),
                     (255, 100, 0), 1)
            cv2.line(debug_frame,
                     (int(w * ZONE_RIGHT_MIN), 0),
                     (int(w * ZONE_RIGHT_MIN), h),
                     (255, 100, 0), 1)

            # Status label
            label = f"MOTION: {zone}" if motion_detected else "idle"
            color = (0, 255, 100) if motion_detected else (180, 180, 180)
            cv2.putText(debug_frame, label,
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, color, 2)

        return motion_detected, zone, debug_frame


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    cv2.startWindowThread()   # required on Pi for CV2 GUI event loop
    debug_mode = args.debug

    print("=" * 52)
    print("  Bee Motion Video System")
    print(f"  Mode    : {'DEBUG' if debug_mode else 'KIOSK'}")
    print(f"  Idle    : {VIDEO_IDLE.name}")
    print(f"  React 1 : {VIDEO_REACT_1.name}")
    print(f"  React 2 : {VIDEO_REACT_2.name}")
    print("  Press D to toggle debug | Ctrl+C to quit")
    print("=" * 52)

    grabber = FrameGrabber(CAMERA_DEVICE, CAPTURE_WIDTH, CAPTURE_HEIGHT)

    # ── Wait for camera to produce first valid frame ─────────────────────────
    print("[INIT] Waiting for camera warm-up...")
    warmup_start = time.time()
    while grabber.get_latest_frame() is None:
        time.sleep(0.05)
        if time.time() - warmup_start > 5.0:
            print("[ERROR] Camera failed to produce a frame after 5s - check /dev/video0")
            grabber.stop()
            sys.exit(1)
    elapsed = time.time() - warmup_start
    print(f"[INIT] Camera ready in {elapsed:.2f}s - OK")

    player   = BeePlayer(fullscreen=not debug_mode)
    detector = MotionDetector()

    last_motion_time = 0.0
    frame_count      = 0

    # Pre-create the named debug window once so it doesn't flicker
    if debug_mode:
        cv2.namedWindow("Bee Debug — press D to toggle", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Bee Debug — press D to toggle", 640, 480)

    try:
        while True:
            # ── Camera health watchdog ────────────────────────────────────────
            cam_age = grabber.age()
            if cam_age > FRAME_STALE_LIMIT:
                print(f"[WARN] Camera stale for {cam_age:.1f}s")

            frame = grabber.get_latest_frame()
            if frame is None:
                time.sleep(0.1)
                continue

            # ── Motion detection ──────────────────────────────────────────────
            motion, zone, dbg_frame = detector.process(frame, debug_mode)

            # ── State machine ─────────────────────────────────────────────────
            now             = time.time()
            cooldown_active = (now - last_motion_time) < MOTION_COOLDOWN

            if motion and player.is_idle and not cooldown_active:
                player.trigger_reaction()
                last_motion_time = now

            player.poll()   # check if reaction ended -> back to idle

            # ── Debug window ──────────────────────────────────────────────────
            if debug_mode and dbg_frame is not None:
                cv2.imshow("Bee Debug — press D to toggle", dbg_frame)

            # ── Key handling ──────────────────────────────────────────────────
            key = cv2.waitKey(1) & 0xFF

            if key == ord('d') or key == ord('D'):
                debug_mode = not debug_mode
                print(f"[DEBUG] Mode toggled -> {'ON' if debug_mode else 'OFF'}")
                if debug_mode:
                    cv2.namedWindow("Bee Debug — press D to toggle", cv2.WINDOW_NORMAL)
                    cv2.resizeWindow("Bee Debug — press D to toggle", 640, 480)
                else:
                    cv2.destroyAllWindows()

            elif key == ord('q') or key == 27:   # Q or ESC
                print("[QUIT] User requested exit.")
                break

            frame_count += 1
            if frame_count % 150 == 0:
                print(f"[HEARTBEAT] frames={frame_count} "
                      f"cam_age={grabber.age():.2f}s "
                      f"player={player._state} "
                      f"debug={'ON' if debug_mode else 'OFF'}")

            time.sleep(MAIN_LOOP_SLEEP)

    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Ctrl+C received.")

    finally:
        print("[CLEANUP] Stopping player and camera...")
        player.stop()
        grabber.stop()
        cv2.destroyAllWindows()
        print("[CLEANUP] Done. Goodbye.")


if __name__ == "__main__":
    main()