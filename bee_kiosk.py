import sys
import time
import threading
from pathlib import Path

import cv2
import numpy as np
import vlc
from PyQt5 import QtCore, QtGui, QtWidgets


# ---------------- PATHS ----------------
VIDEO_DIR = Path("/home/beedisplay/motion_project/videos")

VIDEOS = {
    # loops
    ("centre", "loop"): str(VIDEO_DIR / "loop_centre.mp4"),
    ("left",   "loop"): str(VIDEO_DIR / "loop_left.mp4"),
    ("right",  "loop"): str(VIDEO_DIR / "loop_right.mp4"),

    # transitions (one-shots)
    ("centre", "left"):   str(VIDEO_DIR / "trans_centre_to_left.mp4"),
    ("left",   "centre"): str(VIDEO_DIR / "trans_left_to_centre.mp4"),
    ("centre", "right"):  str(VIDEO_DIR / "trans_centre_to_right.mp4"),
    ("right",  "centre"): str(VIDEO_DIR / "trans_right_to_centre.mp4"),
}

# ---------------- CAMERA ----------------
CAM_INDEX = 0

# Overlay/detection resolution (overlay scales to full screen)
DETECT_W, DETECT_H = 640, 360
TICK_FPS = 20

# Detection tuning
THRESH_BINARY = 128
DILATE_ITERS = 2
ERODE_ITERS = 1

MIN_BLOB_AREA = 400        # blob cutoff (px^2)
PRESENCE_AREA = 1200       # sum blob area threshold (px^2)
PRESENCE_HOLD = 4.0        # seconds to "hold" presence after last detection
ZONE_STABLE_FRAMES = 4     # frames required to confirm a zone change
EDGE_PAD_RATIO = 0.08      # deadband around zone edges to prevent chatter

# Overlay opacity (0..255)
DEFAULT_CAM_ALPHA = 90
DEFAULT_MASK_ALPHA = 70

# ---------------- VLC ----------------
VLC_ARGS = [
    "--fullscreen",
    "--intf", "dummy",
    "--no-video-title-show",
    "--quiet",
    "--file-caching=150",
    "--network-caching=150",
    "--vout=xcb_x11",
]


class BeePlayer:
    """
    Playback state machine:
      - loop modes: centre/left/right
      - transitions route via centre if needed
    """
    def __init__(self, videos: dict):
        self.instance = vlc.Instance(*VLC_ARGS)
        if self.instance is None:
            raise RuntimeError("VLC failed to init (bad VLC_ARGS?)")

        self.player = self.instance.media_player_new()

        # Preload media
        self.media = {}
        for k, v in videos.items():
            m = self.instance.media_new_path(v)
            # Try to make loops repeat (not always honored on all builds, but ok)
            if k[1] == "loop":
                m.add_option(":input-repeat=-1")
            self.media[k] = m

        self.lock = threading.Lock()
        self.mode = "centre"
        self.busy = False
        self.looping = True
        self.next_mode = "centre"
        self.desired_mode = "centre"

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

    def transition_to(self, target_mode: str):
        with self.lock:
            if self.busy:
                return
            if target_mode == self.mode:
                return

            # direct transition
            if (self.mode, target_mode) in self.media:
                self.busy = True
                self.next_mode = target_mode
                self._play((self.mode, target_mode), looping=False)
                print(f"[VLC] TRANS -> {self.mode} to {target_mode}")
                return

            # route via centre
            if self.mode != "centre" and (self.mode, "centre") in self.media:
                self.busy = True
                self.next_mode = "centre"
                self._play((self.mode, "centre"), looping=False)
                print(f"[VLC] TRANS (route) -> {self.mode} to centre")
                return

            # fallback
            print("[VLC] WARNING: missing transition; forcing loop")
            self._play_loop(target_mode)

    def _on_end(self, event):
        # transitions end -> land on loop
        with self.lock:
            if self.looping:
                # sometimes loop clips still end; restart them
                self._play((self.mode, "loop"), looping=True)
                return
            target = self.next_mode
        self._play_loop(target)


def compute_zone_from_mask(mask, zone_width, edge_pad):
    """
    Returns: zone(str or None), total_area(float), centroid_x(float or None), blob_count(int)
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    total_area = 0.0
    weighted_x_sum = 0.0
    blob_count = 0

    boxes = []  # for debug drawing: (x,y,w,h,cx)
    for c in contours:
        a = cv2.contourArea(c)
        if a < MIN_BLOB_AREA:
            continue
        x, y, w, h = cv2.boundingRect(c)
        cx = x + w / 2.0
        total_area += a
        weighted_x_sum += cx * a
        blob_count += 1
        boxes.append((x, y, w, h, cx))

    if total_area <= 0:
        return None, 0.0, None, 0, boxes

    centroid_x = weighted_x_sum / total_area

    left_trigger = zone_width - edge_pad
    right_trigger = (zone_width * 2) + edge_pad

    if centroid_x < left_trigger:
        zone = "left"
    elif centroid_x > right_trigger:
        zone = "right"
    else:
        zone = "centre"

    return zone, total_area, centroid_x, blob_count, boxes


class KioskOverlay(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        # ----- Transparent always-on-top overlay window -----
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        # click-through (mouse), but we'll grab keyboard so we can toggle overlay
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        self.showFullScreen()

        # Grab keyboard so 'O' works even when VLC has focus
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.activateWindow()
        self.raise_()
        self.grabKeyboard()

        # ----- VLC player -----
        for k, p in VIDEOS.items():
            if not Path(p).exists():
                raise FileNotFoundError(f"Missing video for {k}: {p}")

        self.player = BeePlayer(VIDEOS)
        self.player.start()

        # ----- Camera -----
        self.cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        time.sleep(0.3)

        if not self.cap.isOpened():
            raise RuntimeError("Camera not available")

        ret, _ = self.cap.read()
        if not ret:
            raise RuntimeError("Camera opened but no frames received")

        print("Camera mode:",
              self.cap.get(cv2.CAP_PROP_FRAME_WIDTH),
              self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
              self.cap.get(cv2.CAP_PROP_FPS))

        # ----- Detection -----
        self.bgs = cv2.createBackgroundSubtractorMOG2(
            history=300,
            varThreshold=32,
            detectShadows=False
        )
        self.warmup_until = time.time() + 2.0

        self.zone_w = DETECT_W // 3
        self.edge_pad = int(self.zone_w * EDGE_PAD_RATIO)

        self.present = False
        self.last_seen = 0.0
        self.stable_zone = None
        self.stable_count = 0
        self.last_committed_zone = "centre"

        # ----- Overlay toggles -----
        self.overlay_enabled = True
        self.show_camera = True
        self.show_mask = True
        self.show_debug = True

        self.cam_alpha = DEFAULT_CAM_ALPHA
        self.mask_alpha = DEFAULT_MASK_ALPHA

        self.overlay_img = None
        self.debug_line = ""

        # Timer tick
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(int(1000 / TICK_FPS))

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        k = e.key()

        if k in (QtCore.Qt.Key_Q, QtCore.Qt.Key_Escape):
            self.close()
            return

        if k == QtCore.Qt.Key_O:
            self.overlay_enabled = not self.overlay_enabled
            print(f"\n[OVERLAY] enabled={self.overlay_enabled}")
            return

        if k == QtCore.Qt.Key_C:
            self.show_camera = not self.show_camera
            print(f"\n[OVERLAY] show_camera={self.show_camera}")
            return

        if k == QtCore.Qt.Key_M:
            self.show_mask = not self.show_mask
            print(f"\n[OVERLAY] show_mask={self.show_mask}")
            return

        if k == QtCore.Qt.Key_D:
            self.show_debug = not self.show_debug
            print(f"\n[OVERLAY] show_debug={self.show_debug}")
            return

    def closeEvent(self, event):
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        try:
            self.timer.stop()
        except Exception:
            pass
        try:
            self.cap.release()
        except Exception:
            pass
        event.accept()

    def tick(self):
        ret, frame = self.cap.read()
        if not ret:
            self.debug_line = "camera read failed"
            self.update()
            return

        small = cv2.resize(frame, (DETECT_W, DETECT_H), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        fg = self.bgs.apply(gray)

        # Warmup: avoid junk detection while learning background
        if time.time() < self.warmup_until:
            fg[:] = 0

        # Mask cleanup
        fg = cv2.GaussianBlur(fg, (5, 5), 0)
        _, fg = cv2.threshold(fg, THRESH_BINARY, 255, cv2.THRESH_BINARY)
        fg = cv2.dilate(fg, None, iterations=DILATE_ITERS)
        fg = cv2.erode(fg, None, iterations=ERODE_ITERS)

        zone, area, cx, blobs, boxes = compute_zone_from_mask(fg, self.zone_w, self.edge_pad)
        nz = int(cv2.countNonZero(fg))

        now = time.time()

        # Update presence (hold timer)
        if zone is not None and area >= PRESENCE_AREA:
            self.last_seen = now

        self.present = (now - self.last_seen) < PRESENCE_HOLD

        # ----- Playback logic -----
        if not self.present:
            self.player.set_desired_mode("centre")
            self.stable_zone = None
            self.stable_count = 0
            self.last_committed_zone = "centre"

            if self.player.mode != "centre" and not self.player.busy:
                self.player.transition_to("centre")
        else:
            if zone is not None:
                if zone == self.stable_zone:
                    self.stable_count += 1
                else:
                    self.stable_zone = zone
                    self.stable_count = 1

                if self.stable_count >= ZONE_STABLE_FRAMES:
                    self.player.set_desired_mode(self.stable_zone)

                    if self.stable_zone != self.last_committed_zone and not self.player.busy:
                        self.player.transition_to(self.stable_zone)
                        self.last_committed_zone = self.stable_zone

        # Catch up if zone changed during transition
        if not self.player.busy and self.player.desired_mode != self.player.mode:
            self.player.transition_to(self.player.desired_mode)

        # ----- Build overlay RGBA -----
        rgba = np.zeros((DETECT_H, DETECT_W, 4), dtype=np.uint8)

        # Zone lines
        cv2.line(rgba, (self.zone_w, 0), (self.zone_w, DETECT_H), (255, 0, 0, 180), 2)
        cv2.line(rgba, (self.zone_w * 2, 0), (self.zone_w * 2, DETECT_H), (255, 0, 0, 180), 2)

        # Camera layer (transparent)
        if self.show_camera:
            cam_rgba = cv2.cvtColor(small, cv2.COLOR_BGR2BGRA)
            cam_rgba[:, :, 3] = self.cam_alpha
            rgba = cv2.addWeighted(rgba, 1.0, cam_rgba, 1.0, 0)

        # Mask layer (green)
        if self.show_mask:
            mask_rgba = np.zeros((DETECT_H, DETECT_W, 4), dtype=np.uint8)
            mask_rgba[:, :, 1] = fg
            mask_rgba[:, :, 3] = (fg > 0).astype(np.uint8) * self.mask_alpha
            rgba = cv2.addWeighted(rgba, 1.0, mask_rgba, 1.0, 0)

        # Blob boxes
        for (x, y, w, h, cxx) in boxes:
            cv2.rectangle(rgba, (x, y), (x + w, y + h), (0, 255, 255, 220), 2)
            cv2.circle(rgba, (int(cxx), int(y + h / 2)), 4, (0, 255, 255, 220), -1)

        # Centroid marker + zone label
        if cx is not None:
            cv2.circle(rgba, (int(cx), DETECT_H // 2), 8, (0, 0, 255, 220), -1)

        zone_label = zone.upper() if zone else "NONE"
        # Background box for text
        cv2.rectangle(rgba, (10, 18), (410, 76), (0, 0, 0, 140), -1)
        cv2.putText(rgba, f"ZONE: {zone_label}", (20, 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255, 230), 2, cv2.LINE_AA)

        self.debug_line = f"zone={zone} blobs={blobs} area={int(area)} nz={nz} present={self.present} mode={self.player.mode}"

        self.overlay_img = rgba if self.overlay_enabled else None
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)

        if self.overlay_img is not None:
            h, w, _ = self.overlay_img.shape
            qimg = QtGui.QImage(self.overlay_img.data, w, h, QtGui.QImage.Format_ARGB32)
            pix = QtGui.QPixmap.fromImage(qimg)
            painter.drawPixmap(self.rect(), pix)

        # Debug text (optional) – drawn even if overlay disabled
        if self.show_debug:
            painter.setPen(QtGui.QColor(255, 255, 255, 230))
            painter.setFont(QtGui.QFont("DejaVu Sans", 14))
            painter.drawText(20, 30, self.debug_line)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = KioskOverlay()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
