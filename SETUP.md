# Bee Motion Video Kiosk — Full Setup & Recovery Playbook

**Project:** LED Bee Motion Project
**Version:** v1.0-stable
**Date:** April 2026
**Author:** Lowell Smidteboom

---

## Project Overview

The Bee Motion Video Kiosk is a Raspberry Pi-based interactive video playback system.
It monitors a space with a USB camera, detects human motion in left / centre / right
zones, and responds by playing reaction videos. When idle, it loops a main animation
continuously.

**Behaviour:**
- Idle: loops `idle.mp4` continuously
- Motion detected: randomly plays `react_1.mp4` or `react_2.mp4`
- After reaction: automatically returns to idle loop
- Requires 2 consecutive motion frames before triggering (prevents false triggers)
- 2-second cooldown between triggers

**Modes:**
- Kiosk mode (default): fullscreen video, no overlays
- Debug mode (`--debug`): camera feed window with motion contours and zone lines

---

## Hardware Requirements

| Component | Specification |
|---|---|
| Raspberry Pi | Pi 4, 1GB RAM minimum (2GB recommended) |
| Camera | PTZOptics USB webcam or compatible USB camera with MJPEG support |
| Camera device | `/dev/video0` |
| Display | HDMI connected monitor or TV |
| Storage | MicroSD 16GB minimum (32GB recommended) |
| Keyboard | USB keyboard for initial setup and Ctrl+Shift+Q exit |

---

## Video File Requirements

Place all video files in the `videos/` folder of the project:

| File | Purpose |
|---|---|
| `videos/idle.mp4` | Main loop — plays continuously when no motion detected |
| `videos/react_1.mp4` | Reaction video option 1 — triggered randomly on motion |
| `videos/react_2.mp4` | Reaction video option 2 — triggered randomly on motion |

- Encoding: **H.264** (required for Pi GPU hardware decode)
- Resolution: any (system captures at 320x240 for detection only, VLC plays at native res)
- Audio: not used (disabled in VLC for performance)

---

## Step 1 — Flash Pi OS

1. Download **Raspberry Pi Imager** from https://raspberrypi.com/software
2. Select OS: **Raspberry Pi OS with Desktop (Bookworm, 32-bit)**
3. Click the gear icon and configure:

| Setting | Value |
|---|---|
| Hostname | `raspberrypi` |
| Username | `beedisplay` |
| Password | *(your choice)* |
| Enable SSH | Yes |
| WiFi | *(configure if needed)* |

4. Flash to MicroSD and boot the Pi

---

## Step 2 — System Dependencies

SSH into the Pi and run:

```bash
# Update package list
sudo apt update && sudo apt upgrade -y

# Install all required packages
sudo apt install -y \
  python3-full \
  python3-venv \
  python3-opencv \
  git \
  vlc \
  v4l2-utils \
  xbindkeys \
  xterm \
  lxterminal \
  ffmpeg
```

---

## Step 3 — GPU Memory Split

VLC requires at least 128MB GPU memory for hardware H.264 decode.
256MB is recommended.

```bash
# Edit the Pi firmware config
sudo nano /boot/firmware/config.txt
```

Find or add this line:
```
gpu_mem=256
```

Save and reboot:
```bash
sudo reboot
```

Verify after reboot:
```bash
vcgencmd get_mem gpu
# Expected output: gpu=256M
```

---

## Step 4 — Clone the Repository

```bash
cd ~
git clone <your-repo-url> projects/LED_Bee_motion_project-
cd projects/LED_Bee_motion_project-
```

> **Note:** If setting up the remote for the first time on the original Pi:
> ```bash
> git remote add origin https://github.com/<youruser>/<yourrepo>.git
> git push -u origin main
> ```

Verify the branch and tag:
```bash
git checkout main
git log --oneline | head -5
# Should show v1.0-stable commits
```

---

## Step 5 — Virtual Environment Setup

```bash
cd ~/projects/LED_Bee_motion_project-

# Create venv with access to system packages (opencv is system-installed)
python3 -m venv .venv --system-site-packages

# Activate
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Verify
python3 -c "import cv2, vlc; print('Imports OK')"
```

---

## Step 6 — Restore Config Files

Run each block below in order:

### 6a — Kiosk Autostart on Boot

```bash
mkdir -p ~/.config/autostart

cat > ~/.config/autostart/bee-kiosk.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Bee Kiosk
Comment=Auto-launch Bee Motion Video System on login
Exec=bash -c "sleep 5 && cd /home/beedisplay/projects/LED_Bee_motion_project- && source .venv/bin/activate && nice -n 10 python3 -u bee_system.py >> /home/beedisplay/bee_kiosk.log 2>&1"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
```

### 6b — LXDE Autostart (screensaver disable + xbindkeys)

```bash
mkdir -p ~/.config/lxsession/LXDE

cat > ~/.config/lxsession/LXDE/autostart << 'EOF'
@lxpanel --profile LXDE
@pcmanfm --desktop --profile LXDE
@xscreensaver -no-splash
@xbindkeys
@xset s off
@xset s noblank
@xset dpms 0 0 0
EOF
```

### 6c — xbindkeys Hotkey (Ctrl+Shift+Q to exit kiosk)

```bash
cat > ~/.xbindkeysrc << 'EOF'
# Ctrl+Shift+Q — clean exit from Bee kiosk
"pkill -SIGTERM -f bee_system.py"
  Control+Shift+q
EOF
```

### 6d — PCManFM File Manager (disable execute prompt)

```bash
mkdir -p ~/.config/pcmanfm/default

# Disable "Execute File?" prompt on desktop icon double-click
sed -i 's/quick_exec=0/quick_exec=1/' \
  ~/.config/pcmanfm/default/pcmanfm.conf 2>/dev/null || \
  echo -e "[config]\nquick_exec=1" > ~/.config/pcmanfm/default/pcmanfm.conf

mkdir -p ~/.config/libfm
echo -e "[config]\nquick_exec=1" > ~/.config/libfm/libfm.conf
```

### 6e — Make run.sh Executable

```bash
chmod +x ~/projects/LED_Bee_motion_project-/run.sh
```

---

## Step 7 — Desktop Icons

```bash
# Copy icons to desktop
cp ~/projects/LED_Bee_motion_project-/BeeSystem-*.desktop ~/Desktop/

# Make executable
chmod +x ~/Desktop/BeeSystem-Run.desktop
chmod +x ~/Desktop/BeeSystem-Debug.desktop
chmod +x ~/Desktop/BeeSystem-Stop.desktop

# Mark as trusted (suppresses security prompt)
gio set ~/Desktop/BeeSystem-Run.desktop metadata::trusted true
gio set ~/Desktop/BeeSystem-Debug.desktop metadata::trusted true
gio set ~/Desktop/BeeSystem-Stop.desktop metadata::trusted true
```

**Desktop icons:**

| Icon | Action |
|---|---|
| `BeeSystem-Run` | Launch kiosk fullscreen |
| `BeeSystem-Debug` | Launch in debug mode with camera overlay |
| `BeeSystem-Stop` | Kill the running kiosk cleanly |

---

## Step 8 — Verify Everything Works

```bash
# Check camera is detected
v4l2-ctl --list-devices

# Check camera supports MJPEG at 320x240
v4l2-ctl --device=/dev/video0 --list-formats-ext | grep -A5 "320x240\|MJPEG"

# Check GPU memory
vcgencmd get_mem gpu
# Expected: gpu=256M

# Check VLC is installed
vlc --version | head -1

# Test xbindkeys
pgrep xbindkeys && echo "xbindkeys OK" || echo "xbindkeys NOT running — run: xbindkeys"

# Run a quick syntax check on the script
cd ~/projects/LED_Bee_motion_project-
source .venv/bin/activate
python3 -c "import ast; ast.parse(open('bee_system.py').read()); print('bee_system.py syntax OK')"
```

---

## Step 9 — Reboot and Confirm

```bash
sudo reboot
```

After ~30 seconds, SSH back in and verify:

```bash
# Is the kiosk running?
pgrep -a python3 && echo "Kiosk running OK"

# Check the boot log
tail -20 ~/bee_kiosk.log

# Is xbindkeys running for Ctrl+Shift+Q?
pgrep xbindkeys && echo "xbindkeys OK"

# System health check
vcgencmd measure_temp
vcgencmd get_throttled
# get_throttled should return 0x0
```

---

## Daily Operation

### Starting the System
- **Auto:** Starts automatically ~5 seconds after desktop loads on boot
- **Manual:** Double-click `BeeSystem-Run` desktop icon
- **Terminal:** `cd ~/projects/LED_Bee_motion_project- && ./run.sh`

### Stopping the System

| Method | How to use |
|---|---|
| **Ctrl+Shift+Q** | Press on attached keyboard — works anytime |
| **Desktop icon** | Double-click `BeeSystem-Stop` |
| **SSH** | `pkill -f bee_system.py` |
| **Ctrl+C** | In the terminal window if launched manually |

### Debug Mode

```bash
./run.sh --debug
```

Shows:
- Live camera feed window (640x480)
- Green contours around detected motion
- Blue zone divider lines (left / centre / right)
- Text overlay showing motion zone and state

Press **D** at runtime to toggle debug mode on/off without restarting.

### Checking the Log

```bash
tail -50 ~/bee_kiosk.log
```

---

## Troubleshooting

### Kiosk Not Starting on Boot

```bash
# Check autostart file exists
cat ~/.config/autostart/bee-kiosk.desktop

# Check log for errors
cat ~/bee_kiosk.log

# Try running manually
cd ~/projects/LED_Bee_motion_project-
source .venv/bin/activate
DISPLAY=:0 XAUTHORITY=/home/beedisplay/.Xauthority python3 -u bee_system.py
```

### Camera Not Detected

```bash
# List video devices
ls /dev/video*

# Check camera details
v4l2-ctl --list-devices

# Test camera directly
DISPLAY=:0 python3 -c "
import cv2
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
print('Opened:', cap.isOpened())
ret, frame = cap.read()
print('Frame:', ret, frame.shape if ret else None)
cap.release()
"
```

### VLC Not Playing Video

```bash
# Test VLC directly
DISPLAY=:0 cvlc /home/beedisplay/projects/LED_Bee_motion_project-/videos/idle.mp4 --play-and-exit

# Check GPU memory (needs 256MB)
vcgencmd get_mem gpu

# Check video file exists and is readable
ls -lh ~/projects/LED_Bee_motion_project-/videos/
```

### Motion Detection Too Sensitive (Too Many False Triggers)

Edit `bee_system.py` CONFIG section:

```python
MOG2_THRESHOLD = 35    # increase from 25 (less sensitive)
MIN_AREA       = 500   # increase from 300 (larger motion required)
MOTION_COOLDOWN = 3.0  # increase from 2.0 (longer gap between triggers)
```

### Motion Detection Not Sensitive Enough (Missing People at Distance)

Edit `bee_system.py` CONFIG section:

```python
MOG2_THRESHOLD = 15    # decrease from 25 (more sensitive)
MIN_AREA       = 150   # decrease from 300 (smaller motion counts)
```

### Screen Going Blank

```bash
# Apply immediately
DISPLAY=:0 xset s off
DISPLAY=:0 xset s noblank
DISPLAY=:0 xset dpms 0 0 0
DISPLAY=:0 xset -dpms

# Make permanent — check LXDE autostart
cat ~/.config/lxsession/LXDE/autostart
# Should contain the @xset lines from Step 6b
```

### Ctrl+Shift+Q Not Working

```bash
# Check xbindkeys is running
pgrep xbindkeys || xbindkeys

# Check keybinding file
cat ~/.xbindkeysrc

# Manually kill if needed
pkill -f bee_system.py
```

### High CPU Usage / Stuttering

```bash
# Check throttling (should be 0x0)
vcgencmd get_throttled

# Check temperature (should be under 75C)
vcgencmd measure_temp

# Check GPU memory (should be 256M)
vcgencmd get_mem gpu

# Always launch via run.sh (uses nice -n 10)
./run.sh
```

---

## Key Configuration Values

All tunable values are in the `CONFIG` section at the top of `bee_system.py`:

| Setting | Value | Description |
|---|---|---|
| `CAPTURE_WIDTH` | 320 | Camera capture width in pixels |
| `CAPTURE_HEIGHT` | 240 | Camera capture height in pixels |
| `DETECT_SCALE` | 0.5 | Downscale factor for detection (160x120) |
| `DETECT_INTERVAL` | 0.10 | Seconds between detection cycles (10fps) |
| `MOG2_HISTORY` | 300 | Background model history frames |
| `MOG2_THRESHOLD` | 25 | Motion sensitivity (lower = more sensitive) |
| `MIN_AREA` | 300 | Minimum contour area to count as motion |
| `MOTION_COOLDOWN` | 2.0 | Seconds between reaction triggers |
| `FRAME_STALE_LIMIT` | 3.0 | Seconds before camera stall warning |
| `MAIN_LOOP_SLEEP` | 0.10 | Kiosk mode loop rate (10Hz) |
| `DEBUG_LOOP_SLEEP` | 0.033 | Debug mode loop rate (30Hz) |
| `ZONE_LEFT_MAX` | 0.33 | Left zone boundary (0.0-1.0) |
| `ZONE_RIGHT_MIN` | 0.67 | Right zone boundary (0.0-1.0) |

---

## Git Reference

```bash
# Current state
git branch        # main
git tag           # v1.0-stable

# Pull latest changes
cd ~/projects/LED_Bee_motion_project-
git pull

# After editing bee_system.py
git add bee_system.py
git commit -m "your message here"
git push

# Roll back to stable version
git checkout v1.0-stable

# Check history
git log --oneline
```

---

## Quick Reference Card

```
======================================================
  BEE MOTION KIOSK — QUICK REFERENCE
======================================================

  START       Auto on boot, or ./run.sh
  DEBUG       ./run.sh --debug
  STOP        Ctrl+Shift+Q  |  Stop desktop icon
              pkill -f bee_system.py  (via SSH)

  LOG         tail -f ~/bee_kiosk.log

  CAMERA      /dev/video0  (PTZOptics USB)
  VIDEOS      videos/idle.mp4
              videos/react_1.mp4
              videos/react_2.mp4

  HEALTH
    vcgencmd measure_temp     (keep under 75C)
    vcgencmd get_throttled    (should be 0x0)
    vcgencmd get_mem gpu      (should be 256M)

  SENSITIVITY (edit bee_system.py CONFIG)
    MOG2_THRESHOLD = 25   (lower = more sensitive)
    MIN_AREA = 300        (lower = detect at distance)

======================================================
```
