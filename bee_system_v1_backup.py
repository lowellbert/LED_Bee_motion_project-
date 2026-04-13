import cv2
import time
import vlc
import threading
from pathlib import Path

# ---------------- PATHS ----------------
VIDEO_DIR = Path("/home/beedisplay/motion_project/videos")

VIDEOS = {
    # loops
    ("centre", "loop"): str(VIDEO_DIR / "loop_centre.mp4"),
    ("left",   "loop"): str(VIDEO_DIR / "loop_left.mp4"),
    ("right",  "loop"): str(VIDEO_DIR / "loop_right.mp4"),

    # transitions (one-shots)
    ("centre", "left"):  str(VIDEO_DIR / "trans_centre_to_left.mp4"),
    ("left",   "centre"): str(VIDEO_DIR / "trans_left_to_centre.mp4"),
    ("centre", "right"): str(VIDEO_DIR / "trans_centre_to_right.mp4"),
    ("right",  "centre"): str(VIDEO_DIR / "trans_right_to_centre.mp4"),
}

# ---------------- CAMERA ----------------
CAM_INDEX = 0

# Detection runs at low res for speed
DETECT_W, DETECT_H = 320, 180

# Presence + stability tuning
PRESENCE_AREA = 1200          # raise if false triggers; lower if missing people
PRESENCE_HOLD = 4.0           # seconds to keep "present" after last good detection
ZONE_STABLE_FRAMES = 4        # how many frames zone must be consistent before switching
EDGE_PAD_RATIO = 0.08         # hysteresis to prevent boundary chatter

# Optional local preview (won't work headless over SSH unless X-forwarded)
SHOW_PREVIEW = False
SHOW_DEBUG_WINDOW = True # shows "Bee Debug" window (requires desktop session)
SHOW_MASK_WINDOW = True # shows motion mask window
HEARTBEAT_EVERY_N = 30 # prints heartbeat every N frames
CAMERA_STALE_SECONDS = 2.0
# ---------------- VLC ----------------
VLC_ARGS = [
   # "--fullscreen",
    "--intf", "dummy",
    "--no-video-title-show",
    "--quiet",
    "--file-caching=150",
    "--network-caching=150",
    "--vout=xcb_x11",
    # If you see tearing/glitches on your display, try ONE of these:
    # "--vout=gl",
    # "--vout=xcb_x11",
]


class FrameGrabber:
    def __init__(self, device_index=0, width=640, height=480, fps=30):
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps

        self.cap = None
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

        self.latest_frame = None
        self.latest_timestamp = 0.0
        self.frame_count = 0
        self.read_error_count = 0
        self.started = False

    def open_camera(self):
        cap = cv2.VideoCapture(self.device_index, cv2.CAP_V4L2)

        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera index {self.device_index}")

        # Configure camera
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Optional: print negotiated settings for debug
        actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        print(f"[FrameGrabber] Camera opened: {actual_w}x{actual_h} @ {actual_fps:.2f} fps")

        self.cap = cap

    def start(self):
        if self.started:
            return

        self.open_camera()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._reader_loop, name="FrameGrabber", daemon=True)
        self.thread.start()
        self.started = True
        print("[FrameGrabber] Started")

    def _reader_loop(self):
        print("[FrameGrabber] Reader loop running")
        while not self.stop_event.is_set():
            try:
                ret, frame = self.cap.read()
            except Exception as e:
                self.read_error_count += 1
                print(f"[FrameGrabber] cap.read() exception: {e}")
                time.sleep(0.1)
                continue

            if not ret or frame is None:
                self.read_error_count += 1
                print("[FrameGrabber] cap.read() returned no frame")
                time.sleep(0.02)
                continue

            now = time.time()
            with self.lock:
                self.latest_frame = frame
                self.latest_timestamp = now
                self.frame_count += 1

        print("[FrameGrabber] Reader loop exiting")

    def get_latest_frame(self, copy=True):
        with self.lock:
            if self.latest_frame is None:
                return None, 0.0
            frame = self.latest_frame.copy() if copy else self.latest_frame
            ts = self.latest_timestamp
        return frame, ts

    def age(self):
        with self.lock:
            ts = self.latest_timestamp
        if ts == 0.0:
            return float("inf")
        return time.time() - ts

    def has_frame(self):
        with self.lock:
            return self.latest_frame is not None

    def stop(self, join_timeout=1.0):
        print("[FrameGrabber] Stopping...")
        self.stop_event.set()

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=join_timeout)
            if self.thread.is_alive():
                print("[FrameGrabber] Warning: reader thread did not exit (likely blocked in cap.read())")

        if self.cap is not None:
            try:
                self.cap.release()
                print("[FrameGrabber] Camera released")
            except Exception as e:
                print(f"[FrameGrabber] Error releasing camera: {e}")

        self.started = False
        
class BeePlayer:
    """
    State machine for playback:
    - Always in one of: loop_centre / loop_left / loop_right
    - Plays transition clips to change modes
    - Main loop decides when to transition
    - Main loop also polls VLC for transition completion
    """

    def __init__(self, videos: dict):
        self.instance = vlc.Instance(*VLC_ARGS)
        self.player = self.instance.media_player_new()

        # Preload media objects for faster switches
        # Add input-repeat=-1 ONLY to loop clips so they loop inside VLC
        self.media = {}
        for k, v in videos.items():
            m = self.instance.media_new_path(v)
            if k[1] == "loop":
                m.add_option(":input-repeat=-1")
            self.media[k] = m

        self.lock = threading.Lock()
        self.mode = "centre"          # current loop mode
        self.busy = False             # True while a transition is playing
        self.looping = True           # True while a loop clip is playing
        self.next_mode = "centre"     # target mode after transition ends
        self.desired_mode = "centre"  # requested mode from detection

        self._last_vlc_time = -1
        self._last_vlc_progress_ts = time.time()

    def start(self):
        self._play_loop("centre")

    def set_desired_mode(self, mode: str):
        with self.lock:
            self.desired_mode = mode

    def _play(self, key, looping: bool):
        self.looping = looping
        self.player.stop()
        self.player.set_media(self.media[key])
        self.player.play()

        # Reset playback watchdog timing whenever media changes
        self._last_vlc_time = -1
        self._last_vlc_progress_ts = time.time()

    def _play_loop(self, mode: str):
        self.mode = mode
        self.busy = False
        self.next_mode = mode
        self._play((mode, "loop"), looping=True)
        print(f"[VLC] LOOP -> {mode}")

    def transition_to(self, target_mode: str):
        """
        Requests a transition to target_mode.
        If no direct transition exists (left->right), route via centre.
        """
        with self.lock:
            if self.busy:
                return
            if target_mode == self.mode:
                return

            # direct transition exists?
            if (self.mode, target_mode) in self.media:
                self.busy = True
                self.next_mode = target_mode
                self._play((self.mode, target_mode), looping=False)
                print(f"[VLC] TRANS -> {self.mode} to {target_mode}")
                return

            # route via centre if possible
            if self.mode != "centre" and (self.mode, "centre") in self.media:
                self.busy = True
                self.next_mode = "centre"
                self._play((self.mode, "centre"), looping=False)
                print(f"[VLC] TRANS (route) -> {self.mode} to centre")
                return

            # hard fallback
            print("[VLC] WARNING: Missing transition clip; forcing loop switch")
            self._play_loop(target_mode)

    def poll(self):
        """
        Main-thread polling for transition completion.
        Avoids doing playback control inside VLC callback threads.
        """
        if not self.busy:
            return

        st = self.player.get_state()
        if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
            target = self.next_mode
            print(f"[VLC] Transition complete -> {target}")
            self._play_loop(target)

    def ensure_playing(self):
        """
        Loop watchdog for kiosk installs:
        - restart on bad states
        - restart if playback time stops advancing
        """
        if self.busy:
            return

        if not self.looping:
            return

        st = self.player.get_state()
        if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error, vlc.State.Paused):
            print(f"\n[VLC] Watchdog restart (state={st})")
            self.player.stop()
            self.player.set_media(self.media[(self.mode, "loop")])
            self.player.play()
            self._last_vlc_time = -1
            self._last_vlc_progress_ts = time.time()
            return

        now = time.time()
        t = self.player.get_time()
        if t is None:
            t = -1

        if t != self._last_vlc_time:
            self._last_vlc_time = t
            self._last_vlc_progress_ts = now
        else:
            if (now - self._last_vlc_progress_ts) > 1.0:
                print(f"\n[VLC] Watchdog restart (playback stalled at t={t}ms)")
                self.player.stop()
                self.player.set_media(self.media[(self.mode, "loop")])
                self.player.play()
                self._last_vlc_time = -1
                self._last_vlc_progress_ts = now

def compute_zone_from_mask(mask, zone_width, edge_pad):
    """
    Compute an area-weighted centroid across foreground blobs
    and return (zone, total_area, centroid_x)
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    total_area = 0.0
    weighted_x_sum = 0.0

    for c in contours:
        a = cv2.contourArea(c)
        if a < 40:
            continue
        x, y, w, h = cv2.boundingRect(c)
        cx = x + (w / 2)
        total_area += a
        weighted_x_sum += cx * a

    if total_area <= 0:
        return None, 0, None

    centroid_x = weighted_x_sum / total_area

    left_trigger = zone_width - edge_pad
    right_trigger = (zone_width * 2) + edge_pad

    if centroid_x < left_trigger:
        return "left", total_area, centroid_x
    elif centroid_x > right_trigger:
        return "right", total_area, centroid_x
    else:
        return "centre", total_area, centroid_x


def main():
    # Sanity check video files exist
    for k, p in VIDEOS.items():
        if not Path(p).exists():
            raise FileNotFoundError(f"Missing video for {k}: {p}")

    grabber = None
    player = None

    try:
        # ---- Camera open ----
        # ---- Camera open (threaded grabber) ----
        grabber = FrameGrabber(device_index=CAM_INDEX, width=640, height=480, fps=30)
        grabber.start()

        print("[Main] Waiting for first camera frame...", flush=True)
        startup_deadline = time.time() + 5.0

        while not grabber.has_frame():
            if time.time() > startup_deadline:
                raise RuntimeError("Timed out waiting for first camera frame")

            if SHOW_DEBUG_WINDOW or SHOW_MASK_WINDOW:
                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    return

            time.sleep(0.01)

        actual_w = grabber.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = grabber.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fps = grabber.cap.get(cv2.CAP_PROP_FPS)
        print(f"[CAM] requested=640x480@30, actual={actual_w:.0f}x{actual_h:.0f}@{actual_fps:.0f}", flush=True)

        # ---- Background subtraction ----
        bgs = cv2.createBackgroundSubtractorMOG2(
            history=300,
            varThreshold=32,
            detectShadows=False
        )

        warmup_until = time.time() + 2.0
        zone_width = DETECT_W // 3
        edge_pad = int(zone_width * EDGE_PAD_RATIO)

        # Presence and zone stability state
        present = False
        last_seen = 0.0
        stable_zone = None
        stable_count = 0
        last_committed_zone = "centre"

        player = BeePlayer(VIDEOS)
        player.start()

        print("System running... Ctrl+C to stop.")

        frame_count = 0

        while True:
            frame_count += 1

            frame, frame_ts = grabber.get_latest_frame(copy=True)
            frame_age = grabber.age()

            if frame_count % 30 == 0:
                print(f"\n[FRAME] age={frame_age:.3f}s shape={None if frame is None else frame.shape}", flush=True)

            if frame is None:
                player.ensure_playing()

                if SHOW_DEBUG_WINDOW or SHOW_MASK_WINDOW:
                    if (cv2.waitKey(1) & 0xFF) == ord("q"):
                        break

                time.sleep(0.02)
                continue

            if frame_age > CAMERA_STALE_SECONDS:
                print(f"\n[CAM] WARNING: stale frame age={frame_age:.2f}s", flush=True)

                player.ensure_playing()

                if SHOW_DEBUG_WINDOW:
                    dbg = frame.copy()
                    cv2.putText(
                        dbg,
                        f"CAMERA STALLED age={frame_age:.1f}s",
                        (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 0, 255),
                        2,
                    )
                    cv2.imshow("Bee Debug", dbg)

                if SHOW_MASK_WINDOW:
                    stale_mask = cv2.resize(frame, (DETECT_W, DETECT_H))
                    stale_mask = cv2.cvtColor(stale_mask, cv2.COLOR_BGR2GRAY)
                    cv2.putText(
                        stale_mask,
                        f"STALE {frame_age:.1f}s",
                        (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        255,
                        2,
                    )
                    cv2.imshow("Motion Mask", stale_mask)

                if SHOW_DEBUG_WINDOW or SHOW_MASK_WINDOW:
                    if (cv2.waitKey(1) & 0xFF) == ord("q"):
                        break

                time.sleep(0.05)
                continue

            # Downscale for detection
            small = cv2.resize(frame, (DETECT_W, DETECT_H))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            fg = bgs.apply(gray)
            
            # ---- WARMUP: still pump OpenCV windows so they stay live ----
            if time.time() < warmup_until:
                if SHOW_DEBUG_WINDOW:
                    cv2.imshow("Bee Debug", frame)
                if SHOW_MASK_WINDOW:
                    cv2.imshow("Motion Mask", fg)

                if SHOW_DEBUG_WINDOW or SHOW_MASK_WINDOW:
                    if (cv2.waitKey(1) & 0xFF) == ord("q"):
                        return  # exit main cleanly during warmup

                continue


            # Mask cleanup
            fg = cv2.GaussianBlur(fg, (5, 5), 0)
            _, fg = cv2.threshold(fg, 128, 255, cv2.THRESH_BINARY)
            fg = cv2.dilate(fg, None, iterations=2)
            fg = cv2.erode(fg, None, iterations=1)

            zone, area, cx = compute_zone_from_mask(fg, zone_width, edge_pad)
            nz = cv2.countNonZero(fg)

            present_now = (zone is not None and area >= PRESENCE_AREA)

            # SSH-friendly status line
            print(f"zone={zone} area={area:.0f} nz={nz} present={present_now} cx={cx}",
                  end="\r", flush=True)

            # Heartbeat (prints as a new line occasionally so you can tell it’s alive)
            if frame_count % HEARTBEAT_EVERY_N == 0:
                print(f"\n[HB] frames={frame_count} present_now={present_now} area={area:.0f} nz={nz} zone={zone}",
                      flush=True)

            now = time.time()
            if present_now:
                last_seen = now

            present = (now - last_seen) < PRESENCE_HOLD

            # Desired mode logic
            if not present:
                player.set_desired_mode("centre")
                stable_zone = None
                stable_count = 0
                last_committed_zone = "centre"

                if player.mode != "centre" and not player.busy:
                    player.transition_to("centre")
            else:
                # stabilize zone to avoid jitter
                if zone is not None:
                    if zone == stable_zone:
                        stable_count += 1
                    else:
                        stable_zone = zone
                        stable_count = 1

                    if stable_count >= ZONE_STABLE_FRAMES:
                        player.set_desired_mode(stable_zone)

                        if stable_zone != last_committed_zone and not player.busy:
                            player.transition_to(stable_zone)
                            last_committed_zone = stable_zone

            # Poll VLC transition completion from the main thread
            player.poll()

            # Catch-up desired mode after transition ends
            if not player.busy:
                desired = player.desired_mode
                if desired != player.mode:
                    player.transition_to(desired)

            # VLC watchdog
            player.ensure_playing()

            # ---- Debug visuals (only if enabled) ----
            if SHOW_DEBUG_WINDOW:
                dbg = frame.copy()
                cv2.putText(dbg, f"zone={zone} present={present}", (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                cv2.putText(dbg, f"area={area:.0f} nz={nz} cx={cx}", (10, 55),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                cv2.imshow("Bee Debug", dbg)

            if SHOW_MASK_WINDOW:
                cv2.imshow("Motion Mask", fg)

            if SHOW_DEBUG_WINDOW or SHOW_MASK_WINDOW:
                # allow 'q' to quit cleanly

                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    break

    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C received, shutting down...", flush=True)

    finally:
        print("[CLEANUP] starting", flush=True)

        try:
            if player is not None:
                print("[CLEANUP] stopping VLC player", flush=True)
                try:
                    player.player.stop()
                    print("[CLEANUP] VLC player stopped", flush=True)
                except Exception as e:
                    print(f"[CLEANUP] VLC stop error: {e}", flush=True)
        except Exception as e:
            print(f"[CLEANUP] outer VLC cleanup error: {e}", flush=True)

        try:
            if grabber is not None:
                print("[CLEANUP] stopping grabber", flush=True)
                grabber.stop()
                print("[CLEANUP] grabber stopped", flush=True)
        except Exception as e:
            print(f"[CLEANUP] grabber cleanup error: {e}", flush=True)

        try:
            print("[CLEANUP] destroying windows", flush=True)
            cv2.destroyAllWindows()
            print("[CLEANUP] windows destroyed", flush=True)
        except Exception as e:
            print(f"[CLEANUP] destroyAllWindows error: {e}", flush=True)

        print("[CLEANUP] done", flush=True)

if __name__ == "__main__":
    main()
