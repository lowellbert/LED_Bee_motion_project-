import sys
import time
import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

# -------- Camera / Detection Settings --------
CAM_INDEX = 0
DETECT_W, DETECT_H = 640, 360       # overlay resolution (increase for quality; lower for speed)
FPS = 20                            # overlay update rate

# Presence / Blob tuning
THRESH_BINARY = 128
MIN_BLOB_AREA = 400                 # blob cutoff
DILATE_ITERS = 2
ERODE_ITERS = 1

# Visual tuning
CAM_ALPHA = 90                      # 0..255 transparency of camera overlay
MASK_ALPHA = 70                     # 0..255 transparency of mask overlay
DRAW_ZONES = True

# Zone hysteresis display only (your main logic can differ)
EDGE_PAD_RATIO = 0.08

class OverlayWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        # ---- Make window transparent + always on top + borderless ----
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)  # click-through

        # Fullscreen on the current X display
        self.showFullScreen()

        # ---- Camera setup (V4L2 backend) ----
        self.cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            raise RuntimeError("Overlay: camera failed to open")

        # Background subtractor (blob-friendly)
        self.bgs = cv2.createBackgroundSubtractorMOG2(
            history=300,
            varThreshold=32,
            detectShadows=False
        )
        self.warmup_until = time.time() + 2.0

        # Drawing buffer (ARGB)
        self.overlay_img = None
        self.last_debug = ""

        # Timer to refresh overlay
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(int(1000 / FPS))

    def update_frame(self):
        ret, frame = self.cap.read()
        if not ret:
            self.last_debug = "Camera read failed"
            self.update()
            return

        small = cv2.resize(frame, (DETECT_W, DETECT_H), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        fg = self.bgs.apply(gray)
        if time.time() < self.warmup_until:
            # During warmup just show camera faintly
            fg[:] = 0

        # Mask cleanup
        fg = cv2.GaussianBlur(fg, (5, 5), 0)
        _, fg = cv2.threshold(fg, THRESH_BINARY, 255, cv2.THRESH_BINARY)
        fg = cv2.dilate(fg, None, iterations=DILATE_ITERS)
        fg = cv2.erode(fg, None, iterations=ERODE_ITERS)

        # Find blobs
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Build an RGBA overlay canvas at DETECT resolution
        rgba = np.zeros((DETECT_H, DETECT_W, 4), dtype=np.uint8)

        # 1) Draw semi-transparent camera image
        cam_rgba = cv2.cvtColor(small, cv2.COLOR_BGR2BGRA)
        cam_rgba[:, :, 3] = CAM_ALPHA
        rgba = cv2.addWeighted(rgba, 1.0, cam_rgba, 1.0, 0)

        # 2) Draw semi-transparent mask (green)
        mask_rgba = np.zeros((DETECT_H, DETECT_W, 4), dtype=np.uint8)
        mask_rgba[:, :, 1] = fg  # green channel
        mask_rgba[:, :, 3] = (fg > 0).astype(np.uint8) * MASK_ALPHA
        rgba = cv2.addWeighted(rgba, 1.0, mask_rgba, 1.0, 0)

        # 3) Zone lines + blob boxes
        zone_w = DETECT_W // 3
        edge_pad = int(zone_w * EDGE_PAD_RATIO)
        left_trigger = zone_w - edge_pad
        right_trigger = (zone_w * 2) + edge_pad

        if DRAW_ZONES:
            cv2.line(rgba, (zone_w, 0), (zone_w, DETECT_H), (255, 0, 0, 180), 2)
            cv2.line(rgba, (zone_w * 2, 0), (zone_w * 2, DETECT_H), (255, 0, 0, 180), 2)

        total_area = 0.0
        weighted_x_sum = 0.0
        blob_count = 0

        for c in contours:
            a = cv2.contourArea(c)
            if a < MIN_BLOB_AREA:
                continue
            blob_count += 1
            x, y, w, h = cv2.boundingRect(c)
            cx = x + w / 2.0
            total_area += a
            weighted_x_sum += cx * a

            cv2.rectangle(rgba, (x, y), (x + w, y + h), (0, 255, 255, 220), 2)
            cv2.circle(rgba, (int(cx), int(y + h / 2)), 4, (0, 255, 255, 220), -1)

        zone = None
        centroid_x = None
        if total_area > 0:
            centroid_x = weighted_x_sum / total_area
            if centroid_x < left_trigger:
                zone = "LEFT"
            elif centroid_x > right_trigger:
                zone = "RIGHT"
            else:
                zone = "CENTRE"
            cv2.circle(rgba, (int(centroid_x), DETECT_H // 2), 8, (0, 0, 255, 220), -1)

        nz = int(cv2.countNonZero(fg))
        self.last_debug = f"zone={zone} blobs={blob_count} area={int(total_area)} nz={nz} cx={centroid_x}"

        self.overlay_img = rgba
        self.update()  # triggers paintEvent

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)

        # If we haven't built a frame yet, just draw debug text
        if self.overlay_img is None:
            painter.setPen(QtGui.QColor(255, 255, 255, 220))
            painter.drawText(20, 40, "Overlay starting...")
            return

        # Scale overlay to full screen
        h, w, _ = self.overlay_img.shape
        qimg = QtGui.QImage(self.overlay_img.data, w, h, QtGui.QImage.Format_ARGB32)
        pix = QtGui.QPixmap.fromImage(qimg)

        painter.setOpacity(1.0)
        painter.drawPixmap(self.rect(), pix)

        # Debug text on top
        painter.setPen(QtGui.QColor(255, 255, 255, 230))
        painter.setFont(QtGui.QFont("DejaVu Sans", 14))
        painter.drawText(20, 30, self.last_debug)

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = OverlayWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
