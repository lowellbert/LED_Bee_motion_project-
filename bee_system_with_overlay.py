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

# ---------------- VLC ----------------
VLC_ARGS = [
    "--fullscreen",
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


class BeePlayer:
    """
    State machine for playback:
      - Always in one of: loop_centre / loop_left / loop_right
      - Plays transition clips to change modes
      - Main loop decides when to transition; VLC callback just lands on the next loop
    """
    def __init__(self, videos: dict):
        self.instance = vlc.Instance(*VLC_ARGS)
        self.player = self.instance.media_player_new()

        # Preload media objects for faster switches
        # Add input-repeat=-1 ONLY to loop clips so they never "end"
        self.media = {}
        for k, v in videos.items():
            m = self.instance.media_new_path(v)

            # k is a tuple like ("centre","loop") or ("centre","left")
            if k[1] == "loop":
                m.add_option(":input-repeat=-1")  # loop forever inside VLC

            self.media[k] = m

        self.lock = threading.Lock()
        self.mode = "centre"            # current loop mode
        self.busy = False               # True while a transition is playing
        self.looping = True             # True while a loop clip is playing
        self.next_mode = "centre"       # target mode after transition ends
        self.desired_mode = "centre"    # requested mode from detection

        em = self.player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_end)

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

    def _play_loop(self, mode: str):
        self.mode = mode
        self.busy = False
        self.next_mode = mode
        self._play((mode, "loop"), looping=True)
        print(f"[VLC] LOOP -> {mode}")

    def ensure_playing(self):
        """
        Robust loop watchdog for kiosk installs:
        - Handle VLC ending/stopping/pausing
        - Loop using known loop duration (your test loops are 5.00s)
        - Detect stalled playback (time stops advancing)
        """
        if self.busy:
            return

        # Only apply watchdog to loop clips
        if not self.looping:
            return

        # ---- 1) Restart on bad states (include Paused) ----
        st = self.player.get_state()
        if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error, vlc.State.Paused):
            print(f"\n[VLC] Watchdog restart (state={st})")
            self.player.stop()
            self.player.set_media(self.media[(self.mode, "loop")])
            self.player.play()
            # reset stall tracking
            self._last_vlc_time = 0
            self._last_vlc_progress_ts = time.time()
            return

        # ---- 2) Seamless loop based on known duration ----
        # Your generated loop clips are exactly 5.00 seconds
        LOOP_MS = 5000
        t = self.player.get_time()  # ms (can be -1 on some builds)
        if t is not None and t >= 0:
            # soft loop just before end
            if t >= (LOOP_MS - 120):
                self.player.set_time(0)

        # ---- 3) Stall detection: if time stops advancing near end ----
        now = time.time()
        if not hasattr(self, "_last_vlc_time"):
            self._last_vlc_time = -1
            self._last_vlc_progress_ts = now

        t2 = self.player.get_time()
        if t2 is None:
            t2 = -1

        if t2 != self._last_vlc_time:
            self._last_vlc_time = t2
            self._last_vlc_progress_ts = now
        else:
            # If no progress for >0.6s and we're near end, hard restart
            if (now - self._last_vlc_progress_ts) > 0.6 and t2 > 4500:
                print("\n[VLC] Watchdog restart (stall near end)")
                self.player.stop()
                self.player.set_media(self.media[(self.mode, "loop")])
                self.player.play()
                self._last_vlc_time = 0
                self._last_vlc_progress_ts = now



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

    def _on_end(self, event):
        # VLC thread callback: keep it simple and thread-safe.
        with self.lock:
            if self.looping:
                # loop ended, restart same loop
                self._play((self.mode, "loop"), looping=True)
                return

            # transition ended, land on the target loop
            target = self.next_mode

        # play loop outside lock to keep things smooth
        self._play_loop(target)


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

    cap = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)

    # Prefer MJPEG on USB webcams (less CPU, more stable on Pi)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)

    # Reduce buffering latency
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    time.sleep(0.5)  # give the webcam a moment to settle

    if not cap.isOpened():
        raise RuntimeError("Camera not available (V4L2 open failed)")

    ret, test = cap.read()
    if not ret:
        raise RuntimeError("Camera opened but no frames received")

    print("Camera mode:",
          cap.get(cv2.CAP_PROP_FRAME_WIDTH),
          cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
          cap.get(cv2.CAP_PROP_FPS))

    if not cap.isOpened():
        raise RuntimeError("Camera not available")

    # Background subtraction (more stable than frame-diff for presence)
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

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        # Downscale for detection
        small = cv2.resize(frame, (DETECT_W, DETECT_H))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        fg = bgs.apply(gray)
        if time.time() < warmup_until:
            continue

        # Mask cleanup
        fg = cv2.GaussianBlur(fg, (5, 5), 0)
        _, fg = cv2.threshold(fg, 128, 255, cv2.THRESH_BINARY)
        fg = cv2.dilate(fg, None, iterations=2)
        fg = cv2.erode(fg, None, iterations=1)

        zone, area, cx = compute_zone_from_mask(fg, zone_width, edge_pad)
        # SSH-friendly live status line (updates in-place)
        present_now = (zone is not None and area >= PRESENCE_AREA)
        nz = cv2.countNonZero(fg)
        print(f"zone={zone} area={area:.0f} nz={nz} present={present_now} cx={cx}", end="\r", flush=True)

        now = time.time()

        # Update last_seen if we have meaningful foreground
        if zone is not None and area >= PRESENCE_AREA:
            last_seen = now

        was_present = present
        present = (now - last_seen) < PRESENCE_HOLD

        # Desired mode logic
        if not present:
            # nobody present -> always centre loop
            player.set_desired_mode("centre")

            stable_zone = None
            stable_count = 0
            last_committed_zone = "centre"

            # if we’re not already centre and not in transition, go back to centre
            if player.mode != "centre" and not player.busy:
                player.transition_to("centre")

        else:
            # presence detected: stabilize zone to avoid jitter
            if zone is not None:
                if zone == stable_zone:
                    stable_count += 1
                else:
                    stable_zone = zone
                    stable_count = 1

                if stable_count >= ZONE_STABLE_FRAMES:
                    player.set_desired_mode(stable_zone)

                    # Only transition when zone actually changes
                    if stable_zone != last_committed_zone and not player.busy:
                        player.transition_to(stable_zone)
                        last_committed_zone = stable_zone

        # IMPORTANT: If user changed zones while a transition is playing,
        # this will catch up immediately after we land on the loop.
        if not player.busy:
            desired = player.desired_mode
            if desired != player.mode:
                player.transition_to(desired)


        # Watchdog: restart if VLC stopped/ended unexpectedly
        player.ensure_playing()
        print(".", end="", flush=True)
        if SHOW_PREVIEW:
            preview = cv2.cvtColor(fg, cv2.COLOR_GRAY2BGR)
            cv2.line(preview, (zone_width, 0), (zone_width, DETECT_H), (255, 0, 0), 1)
            cv2.line(preview, (zone_width * 2, 0), (zone_width * 2, DETECT_H), (255, 0, 0), 1)
            if cx is not None:
                cv2.circle(preview, (int(cx), DETECT_H // 2), 6, (0, 255, 0), -1)
            cv2.imshow("Detection Mask", preview)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
