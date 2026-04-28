#!/usr/bin/env python3
"""
bee_system.py - Raspberry Pi 5 Motion-Reactive Video Kiosk
-----------------------------------------------------------
Pi 5 version uses picamera2 for camera capture (not OpenCV VideoCapture).
picamera2 uses the native MIPI CSI pipeline -- zero V4L2 buffer issues,
proper frame rate control, significantly lower CPU than USB camera.

Idle state   : loops idle.mp4 continuously
Motion event : randomly plays react_1.mp4 or react_2.mp4, returns to idle
Debug mode   : --debug flag OR press D at runtime
Fullscreen   : default on, suppressed in debug mode

Usage:
    python3 bee_system.py           # kiosk / production
    python3 bee_system.py --debug   # debug with CV2 overlay
"""

import cv2
import vlc
import time
import threading
import random
import argparse
import sys
import os
import signal
import numpy as np
from pathlib import Path
from picamera2 import Picamera2
from pynput import keyboard

# -- Force display environment for SSH + local HDMI --------------------------
os.environ.setdefault("DISPLAY", ":0")
os.environ.setdefault("XAUTHORITY", "/home/beedisplay/.Xauthority")

# -- Clean shutdown flag ------------------------------------------------------
_shutdown_requested = False

def _handle_signal(signum, frame):
    global _shutdown_requested
    print(f"[SHUTDOWN] Signal {signum} received -- shutting down...")
    _shutdown_requested = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)

def start_keyboard_exit_listener():
    """
    Listens for ESC at kernel level using evdev.
    Bypasses X11, VLC fullscreen, and window focus entirely.
    Works in kiosk mode, debug mode, and everything in between.
    """
    import evdev
    from evdev import InputDevice, categorize, ecodes, list_devices

    def find_keyboards():
        devices = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities()
                if ecodes.EV_KEY in caps:
                    if ecodes.KEY_ESC in caps[ecodes.EV_KEY]:
                        devices.append(dev)
                        print(f"[KeyListener] Found keyboard: {dev.name}")
            except Exception:
                pass
        return devices

    def listen(device):
        global _shutdown_requested
        try:
            for event in device.read_loop():
                if _shutdown_requested:
                    break
                if event.type == ecodes.EV_KEY:
                    key = categorize(event)
                    if key.keystate == key.key_down:
                        if key.keycode == "KEY_ESC":
                            print("[KeyListener] ESC pressed -- shutting down")
                            _shutdown_requested = True
                            return
        except Exception as e:
            print(f"[KeyListener] Error: {e}")

    keyboards = find_keyboards()
    if not keyboards:
        print("[KeyListener] No keyboards found -- ESC exit unavailable")
        return

    for kb in keyboards:
        t = threading.Thread(
            target=listen, args=(kb,), daemon=True, name="KeyListener"
        )
        t.start()

    print(f"[KeyListener] Listening on {len(keyboards)} device(s) -- ESC to exit")


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

VIDEO_IDLE    = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/idle.mp4")
VIDEO_REACT_1 = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/react_1.mp4")
VIDEO_REACT_2 = Path("/home/beedisplay/projects/LED_Bee_motion_project-/videos/react_2.mp4")

# Camera capture -- Pi Camera 3 Wide native resolution modes
# 1536x864 @ 120fps is the sweet spot for this use case
CAPTURE_WIDTH    = 1536
CAPTURE_HEIGHT   = 864
CAPTURE_FPS      = 30

# Detection runs on a downscaled greyscale copy
DETECT_SCALE     = 0.1        # 1536x864 * 0.1 = ~154x87 detection image
DETECT_INTERVAL  = 0.10       # run detection at 10fps max

# Motion detection tuning
MOG2_HISTORY     = 300
MOG2_THRESHOLD   = 25
MIN_AREA         = 300
MOTION_COOLDOWN  = 2.0
FRAME_STALE_LIMIT = 3.0

# Loop timing
MAIN_LOOP_SLEEP  = 0.10       # 10Hz main loop in kiosk mode
DEBUG_LOOP_SLEEP = 0.033      # 30Hz in debug mode

# Zone boundaries (fraction of frame width)
ZONE_LEFT_MAX    = 0.33
ZONE_RIGHT_MIN   = 0.67

# -----------------------------------------------------------------------------
# ARGUMENT PARSING
# -----------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Bee Motion Video System")
parser.add_argument("--debug", action="store_true",
                    help="Enable debug mode: CV2 windows and motion overlays")
args = parser.parse_args()

# -----------------------------------------------------------------------------
# VIDEO FILE VALIDATION
# -----------------------------------------------------------------------------

for vpath in [VIDEO_IDLE, VIDEO_REACT_1, VIDEO_REACT_2]:
    if not vpath.exists():
        print(f"[ERROR] Video file not found: {vpath}")
        sys.exit(1)

# -----------------------------------------------------------------------------
# FRAME GRABBER -- picamera2 based (Pi 5 native)
# -----------------------------------------------------------------------------

class FrameGrabber:
    """
    Pi 5 native camera capture using picamera2 / libcamera pipeline.

    picamera2 runs its own capture thread internally and delivers frames
    via capture_array(). Unlike OpenCV VideoCapture + V4L2, picamera2:
      - Properly honours frame rate configuration
      - Uses the ISP hardware pipeline for debayering
      - Does not spin a busy-loop buffer in userspace
      - Supports Camera Module 3 Wide natively (imx708_wide)

    We run capture_array() in a background thread so the main loop
    never blocks waiting for a frame.
    """
    def __init__(self, width, height, fps):
        print(f"[FrameGrabber] Starting picamera2 at {width}x{height} {fps}fps")
        self._width  = width
        self._height = height
        self._fps    = fps

        self._cam = Picamera2()

        config = self._cam.create_video_configuration(
            main={"size": (width, height), "format": "BGR888"},
            controls={
                "FrameRate": float(fps),
                "AwbEnable": True,
                "AwbMode": 0,
                "Brightness": 0.0,
                "Contrast": 1.05,
                "Saturation": 1.1,
                "Sharpness": 1.2,
                "NoiseReductionMode": 2
            },
            buffer_count=2,
        )
        self._cam.configure(config)
        self._cam.start()
        time.sleep(0.5)
        print("[FrameGrabber] picamera2 started OK")

        self._frame     = None
        self._lock      = threading.Lock()
        self._last_time = time.time()
        self._running   = True
        self._thread    = threading.Thread(
            target=self._run, daemon=True, name="FrameGrabber"
        )
        self._thread.start()
        print("[FrameGrabber] Capture thread started.")

    def _run(self):
        """
        capture_array() blocks until the next frame is ready from the
        hardware pipeline -- no spin, no busy wait, genuinely sleeps
        between frames. This is the key difference from OpenCV V4L2.
        """
        target_interval = 1.0 / self._fps
        while self._running:
            try:
                t0    = time.time()
                frame = self._cam.capture_array("main")
                if frame is not None:
                    frame = self._correct_colour(frame)
                    with self._lock:
                        self._frame     = frame
                        self._last_time = time.time()
                elapsed   = time.time() - t0
                remaining = target_interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
            except Exception as e:
                print(f"[FrameGrabber] Capture error: {e}")
                time.sleep(0.1)

    @staticmethod
    def _correct_colour(frame):
        """
        imx708_wide outputs RGB channel order despite BGR888 format label.
        Single channel swap corrects colours for OpenCV BGR expectation.
        Orange looks orange, faces look natural, whites stay white.
        """
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def get_latest_frame(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def age(self):
        with self._lock:
            return time.time() - self._last_time

    def stop(self):
        self._running = False
        self._thread.join(timeout=2.0)
        self._cam.stop()
        self._cam.close()
        print("[FrameGrabber] Camera released.")


# -----------------------------------------------------------------------------
# BEE PLAYER -- VLC state machine
# -----------------------------------------------------------------------------

class BeePlayer:
    """
    Two-state VLC controller:
      IDLE      -> loops idle.mp4
      REACTING  -> plays reaction video once, then returns to IDLE

    Pi 5 uses --avcodec-hw=any for hardware decode (v4l2m2m).
    Note: --codec=h264_mmal from Pi 4 does NOT exist on Pi 5.
    """
    STATE_IDLE     = "IDLE"
    STATE_REACTING = "REACTING"

    def __init__(self, fullscreen: bool):
        vlc_args = [
            "--no-video-title-show",
            "--quiet",
            "--no-xlib",
            "--avcodec-hw=any",
            "--file-caching=300",
            "--no-audio",
            "--mouse-hide-timeout=3000",
        ]
        if fullscreen:
            vlc_args += ["--fullscreen", "--video-on-top"]
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
        chosen = random.choice([VIDEO_REACT_1, VIDEO_REACT_2])
        media  = self._make_media(chosen)
        self._player.set_media(media)
        self._player.play()
        self._state = self.STATE_REACTING
        print(f"[BeePlayer] -> REACTING ({chosen.name})")

    def poll(self):
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


# -----------------------------------------------------------------------------
# MOTION DETECTOR
# -----------------------------------------------------------------------------

class MotionDetector:
    """
    MOG2 background subtraction on a downscaled greyscale image.
    Returns (motion_detected, zone, debug_frame)
    zone: 'left' | 'centre' | 'right' | None
    """
    def __init__(self):
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=MOG2_HISTORY,
            varThreshold=MOG2_THRESHOLD,
            detectShadows=False,
        )
        self._kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    def process(self, frame, debug_mode: bool):
        h, w  = frame.shape[:2]
        dw    = max(1, int(w * DETECT_SCALE))
        dh    = max(1, int(h * DETECT_SCALE))
        small = cv2.resize(frame, (dw, dh))
        grey  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask  = self._bg.apply(grey)
        mask  = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)

        cnts, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

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
                cx_norm = cx / dw
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
                     (int(w * ZONE_LEFT_MAX), h), (255, 100, 0), 1)
            cv2.line(debug_frame,
                     (int(w * ZONE_RIGHT_MIN), 0),
                     (int(w * ZONE_RIGHT_MIN), h), (255, 100, 0), 1)
            label = f"MOTION: {zone}" if motion_detected else "idle"
            cv2.putText(debug_frame, label, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 255, 100) if motion_detected else (180, 180, 180), 2)

        return motion_detected, zone, debug_frame


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

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

    grabber = FrameGrabber(CAPTURE_WIDTH, CAPTURE_HEIGHT, CAPTURE_FPS)

    print("[INIT] Waiting for camera warm-up...")
    warmup_start = time.time()
    while grabber.get_latest_frame() is None:
        time.sleep(0.05)
        if time.time() - warmup_start > 8.0:
            print("[ERROR] Camera failed to produce a frame after 8s")
            grabber.stop()
            sys.exit(1)
    print(f"[INIT] Camera ready in {time.time() - warmup_start:.2f}s - OK")

    player   = BeePlayer(fullscreen=not debug_mode)
    detector = MotionDetector()
    start_keyboard_exit_listener()

    last_motion_time = 0.0
    last_detect_time = 0.0
    motion           = False
    zone             = None
    dbg_frame        = None
    motion_confirm   = 0
    frame_count      = 0

    
    if debug_mode:
        cv2.namedWindow("Bee Debug -- press D to toggle", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Bee Debug -- press D to toggle", 640, 480)

    try:
        while not _shutdown_requested:

            if grabber.age() > FRAME_STALE_LIMIT:
                print(f"[WARN] Camera stale for {grabber.age():.1f}s")

            frame = grabber.get_latest_frame()
            if frame is None:
                time.sleep(0.1)
                continue

            now = time.time()
            if now - last_detect_time >= DETECT_INTERVAL:
                motion, zone, dbg_frame = detector.process(frame, debug_mode)
                last_detect_time = now
            else:
                time.sleep(0.01)
                continue

            cooldown_active = (now - last_motion_time) < MOTION_COOLDOWN
            if motion:
                motion_confirm = min(motion_confirm + 1, 3)
            else:
                motion_confirm = 0

            if motion_confirm >= 2 and player.is_idle and not cooldown_active:
                player.trigger_reaction()
                last_motion_time = now
                motion_confirm   = 0

            player.poll()

            if debug_mode and dbg_frame is not None:
                cv2.imshow("Bee Debug -- press D to toggle", dbg_frame)

            if debug_mode:
                key = cv2.waitKey(1) & 0xFF
                if key in (ord('d'), ord('D')):
                    debug_mode = not debug_mode
                    print(f"[DEBUG] Mode toggled -> {'ON' if debug_mode else 'OFF'}")
                    if not debug_mode:
                        cv2.destroyAllWindows()
                    else:
                        cv2.namedWindow("Bee Debug -- press D to toggle",
                                        cv2.WINDOW_NORMAL)
                        cv2.resizeWindow("Bee Debug -- press D to toggle",
                                         640, 480)
                elif key in (ord('q'), 27):
                    print("[QUIT] User requested exit.")
                    break

            frame_count += 1
            if frame_count % 150 == 0:
                print(f"[HEARTBEAT] frames={frame_count} "
                      f"cam_age={grabber.age():.2f}s "
                      f"player={player._state} "
                      f"debug={'ON' if debug_mode else 'OFF'}")

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