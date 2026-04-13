#!/usr/bin/env python3
"""
bee_system.py -- Raspberry Pi Motion-Reactive Video Kiosk
----------------------------------------------------------
Idle state   : loops idle.mp4 continuously
Motion event : randomly plays react_1.mp4 or react_2.mp4, then returns to idle
Debug mode   : enabled via --debug flag OR press D at runtime to toggle
Fullscreen   : default on, suppressed in debug mode

Camera capture (v7): OpenCV V4L2 with grab/retrieve pacing strategy.
Sleeps BEFORE cap.grab() to let the camera produce exactly one fresh frame,
then flushes stale buffer frames, then retrieves only the latest.
This prevents the grabber thread from spinning on a pre-filled buffer.

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
import signal

# Allow clean shutdown via pkill or desktop Stop icon
_shutdown_requested = False

def _handle_signal(signum, frame):
    global _shutdown_requested
    print(f"\n[SHUTDOWN] Signal {signum} received — shutting down...")
    _shutdown_requested = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)

# -- Force display environment for SSH + local HDMI use ----------------------
os.environ.setdefault("DISPLAY", ":0")
os.environ.setdefault("XAUTHORITY", "/home/beedisplay/.Xauthority")

# ----------------------------------------------------------------------------
# CONFIG -- edit paths and tuning values here
# ----------------------------------------------------------------------------

VIDEO_IDLE    = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/idle.mp4")
VIDEO_REACT_1 = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/react_1.mp4")
VIDEO_REACT_2 = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/react_2.mp4")

CAMERA_DEVICE     = "/dev/video0"
CAPTURE_WIDTH     = 320        # detection resolution width
CAPTURE_HEIGHT    = 240        # detection resolution height
DETECT_SCALE      = 0.5        # 0.5 on 320x240 = 160x120 detection image
DETECT_INTERVAL   = 0.10       # run motion detection at 10fps max
MOG2_HISTORY      = 300        # faster background model adaptation
MOG2_THRESHOLD    = 25         # foreground sensitivity
MIN_AREA          = 300        # minimum contour area (adjusted for lower res)
MOTION_COOLDOWN   = 2.0        # seconds to ignore new motion after reaction starts
FRAME_STALE_LIMIT = 3.0        # seconds before camera is considered stalled
MAIN_LOOP_SLEEP   = 0.10       # 10Hz main loop in kiosk mode
DEBUG_LOOP_SLEEP  = 0.033      # ~30Hz in debug mode for responsive preview

# Zone boundaries as fraction of detection frame width
ZONE_LEFT_MAX  = 0.33
ZONE_RIGHT_MIN = 0.67

# ----------------------------------------------------------------------------
# ARGUMENT PARSING
# ----------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Bee Motion Video System")
parser.add_argument(
    "--debug", action="store_true",
    help="Enable debug mode: shows CV2 windows and motion overlays"
)
args = parser.parse_args()

# ----------------------------------------------------------------------------
# VALIDATE VIDEO FILES
# ----------------------------------------------------------------------------

for vpath in [VIDEO_IDLE, VIDEO_REACT_1, VIDEO_REACT_2]:
    if not vpath.exists():
        print(f"[ERROR] Video file not found: {vpath}")
        sys.exit(1)

# ----------------------------------------------------------------------------
# FRAME GRABBER -- OpenCV V4L2 with grab/retrieve pacing
# ----------------------------------------------------------------------------

class FrameGrabber:
    """
    OpenCV V4L2 camera capture with enforced frame rate pacing.

    The core problem: OpenCV's internal V4L2 thread fills a ring buffer
    at full camera speed. cap.read() drains this buffer instantly,
    causing the grabber thread to spin at 100% CPU regardless of sleeps
    placed after the read.

    Solution: sleep BEFORE cap.grab() so the buffer only ever contains
    one frame when we read it. cap.grab() discards stale frames, then
    cap.retrieve() decodes only the freshest one.
    This enforces true pacing and prevents buffer drain spinning.
    """

    def __init__(self, device, width, height):
        print("[FrameGrabber] Setting camera to 15fps via v4l2-ctl...")
        ret = os.system(f"v4l2-ctl --device={device} --set-parm=15 2>/dev/null")
        if ret == 0:
            print("[FrameGrabber] v4l2-ctl FPS set OK")
        else:
            print("[FrameGrabber] v4l2-ctl failed -- relying on thread pacing")

        self._cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        print("[FrameGrabber] Requested MJPEG format")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, 15)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self._cap.isOpened():
            raise RuntimeError(f"[FrameGrabber] Cannot open camera: {device}")

        actual_w   = self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h   = self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
        actual_cc  = int(self._cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = (
            chr(actual_cc & 0xFF) +
            chr((actual_cc >> 8)  & 0xFF) +
            chr((actual_cc >> 16) & 0xFF) +
            chr((actual_cc >> 24) & 0xFF)
        )
        print(f"[FrameGrabber] Opened: {actual_w:.0f}x{actual_h:.0f} @ {actual_fps:.1f}fps  format={fourcc_str}")

        self._frame     = None
        self._lock      = threading.Lock()
        self._last_time = time.time()
        self._running   = True
        self._thread    = threading.Thread(target=self._run, daemon=True, name="FrameGrabber")
        self._thread.start()
        print("[FrameGrabber] Capture thread started.")

    def _run(self):
        """
        Pacing strategy:
          1. Sleep for one full frame interval (1/15s) BEFORE reading.
             This lets the camera produce exactly one new frame in the buffer.
          2. Flush any stale buffered frames with a fast grab() loop (no decode).
          3. Decode only the single fresh frame with retrieve().

        By sleeping first, the buffer is always nearly empty when we read,
        so there is no backlog to drain and no spin between cycles.
        """
        interval = 1.0 / 15.0   # 15fps -- camera hardware minimum for this device
        while self._running:
            # Sleep first -- let camera produce exactly one new frame
            time.sleep(interval)

            # Flush any stale frames from the internal buffer
            # grab() is cheap -- no JPEG decode, just advances buffer pointer
            for _ in range(4):
                if not self._cap.grab():
                    break

            # Decode only the most recent grabbed frame
            ret, frame = self._cap.retrieve()
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


# ----------------------------------------------------------------------------
# BEE PLAYER -- VLC state machine
# ----------------------------------------------------------------------------

class BeePlayer:
    """
    Two-state VLC controller:
      IDLE      -> loops idle.mp4 continuously
      REACTING  -> plays a reaction video once, then returns to IDLE

    VLC uses Pi hardware H.264 decode via MMAL to offload from ARM CPU to GPU.
    Falls back to avcodec hardware decode if MMAL is unavailable.
    """
    STATE_IDLE     = "IDLE"
    STATE_REACTING = "REACTING"

    def __init__(self, fullscreen: bool):
        vlc_args = [
            "--no-video-title-show",
            "--quiet",
            "--no-xlib",            # prevents X threading conflicts with OpenCV on Pi
            "--codec=h264_mmal",    # hardware H.264 decode via Pi GPU (MMAL)
            "--avcodec-hw=any",     # fallback: use any available hardware decode
            "--file-caching=300",   # small file cache -- videos are local
            "--no-audio",           # no audio decode overhead
            "--mouse-hide-timeout=3000", # ← hide cursor after 3s of inactivity
		
        ]
        if fullscreen:
            vlc_args += [
                "--fullscreen",
                "--video-on-top",
            ]
        else:
            vlc_args += [
                "--no-fullscreen",
                "--width=800",
                "--height=600",
                "--video-on-top",
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
        media.add_option("input-repeat=65535")
        self._player.set_media(media)
        self._player.play()
        self._state = self.STATE_IDLE
        print("[BeePlayer] -> IDLE loop")

    def trigger_reaction(self):
        """Pick a random reaction video and play it once."""
        chosen = random.choice([VIDEO_REACT_1, VIDEO_REACT_2])
        media  = self._make_media(chosen)
        self._player.set_media(media)
        self._player.play()
        self._state = self.STATE_REACTING
        print(f"[BeePlayer] -> REACTING ({chosen.name})")

    def poll(self):
        """
        Call every main loop cycle.
        Detects end-of-reaction video and transitions back to IDLE.
        """
        if self._state == self.STATE_REACTING:
            st = self._player.get_state()
            if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
                print("[BeePlayer] Reaction ended -> returning to IDLE")
                self._play_idle()

    @property
    def is_idle(self):
        return self._state == self.STATE_IDLE

    def stop(self):
        self._player.stop()
        print("[BeePlayer] Stopped.")


# ----------------------------------------------------------------------------
# MOTION DETECTOR
# ----------------------------------------------------------------------------

class MotionDetector:
    """
    MOG2 background subtraction on a greyscale downscaled image.
    Processing greyscale (1 channel) vs BGR (3 channels) cuts MOG2 cost by ~3x.

    Returns (motion_detected: bool, zone: str or None, debug_frame or None)
    zone is one of: left, centre, right
    """
    def __init__(self):
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=MOG2_HISTORY,
            varThreshold=MOG2_THRESHOLD,
            detectShadows=False
        )
        # Pre-build kernel once -- rect is cheaper than ellipse on ARM
        self._kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    def process(self, frame, debug_mode: bool):
        h, w  = frame.shape[:2]
        dw    = int(w * DETECT_SCALE)
        dh    = int(h * DETECT_SCALE)

        # Downscale then greyscale -- MOG2 on 1 channel is ~3x cheaper
        small = cv2.resize(frame, (dw, dh))
        grey  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask  = self._bg.apply(grey)
        mask  = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)

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
                cx_raw  = M["m10"] / M["m00"]
                cx_norm = cx_raw / dw

                if cx_norm < ZONE_LEFT_MAX:
                    zone = "left"
                elif cx_norm > ZONE_RIGHT_MIN:
                    zone = "right"
                else:
                    zone = "centre"

            if debug_mode and debug_frame is not None:
                scale_x    = w / dw
                scale_y    = h / dh
                cnt_scaled = (cnt * [scale_x, scale_y]).astype(int)
                cv2.drawContours(debug_frame, [cnt_scaled], -1, (0, 255, 0), 2)

        if debug_mode and debug_frame is not None:
            cv2.line(debug_frame,
                     (int(w * ZONE_LEFT_MAX), 0),
                     (int(w * ZONE_LEFT_MAX), h),
                     (255, 100, 0), 1)
            cv2.line(debug_frame,
                     (int(w * ZONE_RIGHT_MIN), 0),
                     (int(w * ZONE_RIGHT_MIN), h),
                     (255, 100, 0), 1)
            label = f"MOTION: {zone}" if motion_detected else "idle"
            color = (0, 255, 100) if motion_detected else (180, 180, 180)
            cv2.putText(debug_frame, label,
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, color, 2)

        return motion_detected, zone, debug_frame


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    cv2.startWindowThread()

    debug_mode = args.debug

    print("=" * 54)
    print("  Bee Motion Video System")
    print(f"  Mode    : {'DEBUG' if debug_mode else 'KIOSK'}")
    print(f"  Idle    : {VIDEO_IDLE.name}")
    print(f"  React 1 : {VIDEO_REACT_1.name}")
    print(f"  React 2 : {VIDEO_REACT_2.name}")
    print("  Press D to toggle debug | Ctrl+C to quit")
    print("=" * 54)

    grabber = FrameGrabber(CAMERA_DEVICE, CAPTURE_WIDTH, CAPTURE_HEIGHT)

    # Wait for camera to produce its first valid frame before starting VLC
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

    # Persistent detection state carried between throttled cycles
    last_motion_time = 0.0
    last_detect_time = 0.0
    motion           = False
    zone             = None
    dbg_frame        = None
    frame_count      = 0

    # Pre-create debug window once to avoid per-frame overhead
    if debug_mode:
        cv2.namedWindow("Bee Debug - press D to toggle", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Bee Debug - press D to toggle", 640, 480)

    try:
        while not _shutdown_requested:
            # camera health watchdog
            cam_age = grabber.age()
            if cam_age > FRAME_STALE_LIMIT:
                print(f"[WARN] Camera stale for {cam_age:.1f}s")

            frame = grabber.get_latest_frame()
            if frame is None:
                time.sleep(0.1)
                continue

            now = time.time()

            # motion detection -- only runs at DETECT_INTERVAL rate
            if now - last_detect_time >= DETECT_INTERVAL:
                motion, zone, dbg_frame = detector.process(frame, debug_mode)
                last_detect_time = now
            else:
                # between detection cycles -- yield CPU
                time.sleep(0.01)
                continue

            # state machine
            cooldown_active = (now - last_motion_time) < MOTION_COOLDOWN

            if motion and player.is_idle and not cooldown_active:
                player.trigger_reaction()
                last_motion_time = now

            player.poll()   # check if reaction ended -> back to IDLE

            # debug window
            if debug_mode and dbg_frame is not None:
                cv2.imshow("Bee Debug - press D to toggle", dbg_frame)

            # key handling -- only pump waitKey in debug mode
            if debug_mode:
                key = cv2.waitKey(1) & 0xFF
                if key == ord("d") or key == ord("D"):
                    debug_mode = not debug_mode
                    print(f"[DEBUG] Mode toggled -> {'ON' if debug_mode else 'OFF'}")
                    if debug_mode:
                        cv2.namedWindow("Bee Debug - press D to toggle", cv2.WINDOW_NORMAL)
                        cv2.resizeWindow("Bee Debug - press D to toggle", 640, 480)
                    else:
                        cv2.destroyAllWindows()
                elif key == ord("q") or key == 27:
                    print("[QUIT] User requested exit.")
                    break

            # heartbeat
            frame_count += 1
            if frame_count % 150 == 0:
                print(
                    f"[HEARTBEAT] frames={frame_count} "
                    f"cam_age={grabber.age():.2f}s "
                    f"player={player._state} "
                    f"debug={'ON' if debug_mode else 'OFF'}"
                )

            # loop sleep -- kiosk runs slower to free CPU for VLC
            time.sleep(DEBUG_LOOP_SLEEP if debug_mode else MAIN_LOOP_SLEEP)

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


# =============================================================================
# run.sh -- recommended launcher script
# Save as run.sh in project root, then: chmod +x run.sh
# Usage:  ./run.sh            (kiosk mode)
#         ./run.sh --debug    (debug mode)
# =============================================================================
# #!/bin/bash
# set -e
# cd ~/projects/LED_Bee_motion_project-
# source .venv/bin/activate
# export DISPLAY=:0
# export XAUTHORITY=/home/beedisplay/.Xauthority
# PYTHONUNBUFFERED=1 python3 -u bee_system.py "$@"
# =============================================================================
