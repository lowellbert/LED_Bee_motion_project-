# 🐝 Bee Motion Video Kiosk

An interactive, motion-reactive video display system built on Raspberry Pi.
The system watches a space with a camera, detects when someone walks by,
and responds by playing a video — automatically, unattended, and silently.

---

## 🎬 What It Does

The kiosk runs in two states:

**When nobody is around:**
The system plays a looping animation (`idle.mp4`) continuously on the screen.
It runs on its own — no interaction needed.

**When someone walks by:**
The camera detects motion and the system instantly switches to one of two
reaction videos (`react_1.mp4` or `react_2.mp4`), chosen at random.
Once the reaction video finishes, the system returns to the idle loop
and waits for the next visitor.

**Zone Detection:**
The camera view is divided into three zones — Left, Centre, and Right.
The system detects which zone the motion occurs in, allowing for future
zone-specific responses if needed.

**False Trigger Protection:**
Motion must be detected in two consecutive checks before a reaction is triggered,
preventing lights, shadows, or brief camera noise from causing unwanted playbacks.

---

## ⚙️ How It Works

```
Power On
   │
   ▼
Idle video loops on screen
   │
   ▼
Camera watches for motion
   │
   ├── No motion detected ──────────────────────► Keep looping idle video
   │
   └── Motion detected (2 confirmations)
          │
          ▼
       Random reaction video plays (react_1 or react_2)
          │
          ▼
       Reaction ends → Return to idle loop
```

---

## 🖥️ Hardware

| Component        | Details                                      |
|------------------|----------------------------------------------|
| Computer         | Raspberry Pi 4 (1GB RAM minimum)             |
| Camera           | PTZOptics USB Webcam — connected to /dev/video0 |
| Display          | Any HDMI monitor or TV                       |
| Storage          | MicroSD card (16GB minimum)                  |
| Keyboard         | USB keyboard (for setup and exit shortcut)   |

---

## 🎥 Video Files

All video files live in the `videos/` folder inside the project:

| File               | Role                                              |
|--------------------|---------------------------------------------------|
| `videos/idle.mp4`  | Plays on loop when no one is present              |
| `videos/react_1.mp4` | Reaction video — triggered randomly on motion   |
| `videos/react_2.mp4` | Reaction video — triggered randomly on motion   |

**Requirements:**
- Format: **H.264 / MP4** (required for smooth playback on Pi)
- Resolution: Any — the display will show the video at full screen
- Audio: Not used — audio is disabled for performance
- Length: Any — idle loops forever, reaction plays once then returns to idle

> To swap in new videos, simply replace the files in the `videos/` folder
> keeping the same filenames, then restart the system.

---

## 🕹️ Daily Operation

### Starting the System
The kiosk **starts automatically** when the Pi powers on.
Allow approximately 10–15 seconds after the desktop appears for it to launch.

To start it manually:
- Double-click the **BeeSystem — Run** icon on the desktop
- Or from a terminal: `./run.sh`

### Stopping the System

| Method                  | How                                          |
|-------------------------|----------------------------------------------|
| **Keyboard shortcut**   | Press `Ctrl + Shift + Q` on attached keyboard |
| **Desktop icon**        | Double-click **BeeSystem — Stop** on desktop  |
| **Remote (SSH)**        | Run `pkill -f bee_system.py`                 |

### Debug Mode
Debug mode shows a live camera feed with motion detection overlays —
useful for checking camera alignment and testing detection sensitivity.

To launch in debug mode:
- Double-click **BeeSystem — Debug** on the desktop
- Or from a terminal: `./run.sh --debug`

While in debug mode, press **D** on the keyboard to toggle the
camera overlay on or off without restarting.

### Checking the System Log
Every boot session is logged to a file on the Pi:

```bash
tail -50 ~/bee_kiosk.log
```

A healthy log looks like:
```
[INIT] Camera ready in 0.75s - OK
[BeePlayer] -> IDLE loop
[BeePlayer] -> REACTING (react_1.mp4)
[BeePlayer] Reaction ended -> returning to IDLE
[HEARTBEAT] frames=300 cam_age=0.06s player=IDLE debug=OFF
```

---

## 🔧 Installation & Setup

For full step-by-step setup instructions see **[SETUP.md](SETUP.md)**.

**Summary of setup steps:**

1. Flash Raspberry Pi OS with Desktop (Bookworm, 32-bit) to a MicroSD card
2. Configure hostname `raspberrypi` and username `beedisplay` in Raspberry Pi Imager
3. Boot the Pi and install system dependencies via Terminal
4. Set GPU memory to 256MB in `/boot/firmware/config.txt`
5. Clone this repository into `~/projects/LED_Bee_motion_project-`
6. Create the Python virtual environment and install requirements
7. Restore config files for autostart, screensaver disable, and exit hotkey
8. Copy desktop icons to `~/Desktop` and mark as trusted
9. Reboot — the system launches automatically

---

## 🎚️ Adjusting Motion Sensitivity

If the system is triggering too easily or missing people at a distance,
open `bee_system.py` in a text editor and find the `CONFIG` section near the top.

These are the three most important settings to adjust:

| Setting            | What it controls                          | Current Value |
|--------------------|-------------------------------------------|---------------|
| `MOG2_THRESHOLD`   | How sensitive the motion detection is     | `25`          |
| `MIN_AREA`         | Minimum size of motion to trigger         | `300`         |
| `MOTION_COOLDOWN`  | Seconds to wait between triggers          | `2.0`         |

**Too many false triggers** (lights, shadows setting it off):
- Increase `MOG2_THRESHOLD` to `35` or higher
- Increase `MIN_AREA` to `500` or higher
- Increase `MOTION_COOLDOWN` to `3.0` or higher

**Not detecting people at distance:**
- Decrease `MOG2_THRESHOLD` to `15`
- Decrease `MIN_AREA` to `150`

After making changes, save the file and restart the system.

---

## 🛠️ Technical Stack

| Component       | Technology                                            |
|-----------------|-------------------------------------------------------|
| Language        | Python 3.13                                           |
| Motion Detection| OpenCV — MOG2 background subtraction (greyscale)      |
| Video Playback  | VLC via python-vlc (hardware H.264 decode via MMAL)   |
| Camera Capture  | OpenCV V4L2 with MJPEG format at 15fps                |
| Threading       | FrameGrabber thread + main loop + VLC internal thread |
| Camera Control  | v4l2-utils for hardware FPS control                   |
| Process Priority| `nice -n 10` — VLC gets CPU priority over grabber     |

**Key Files:**

| File                   | Purpose                                         |
|------------------------|-------------------------------------------------|
| `bee_system.py`        | Main application — all logic lives here         |
| `run.sh`               | Production launcher (sets nice level and env)   |
| `requirements.txt`     | Python package dependencies                     |
| `SETUP.md`             | Full setup and recovery playbook                |
| `videos/`              | Folder containing all video files               |
| `BeeSystem-*.desktop`  | Desktop launcher icons                          |
| `bee-kiosk.desktop`    | Autostart entry for boot launch                 |

**Git:**
- Branch: `main`
- Stable tag: `v1.0-stable`
- Development was done on `feature/v2-motion-react`, merged to `main`

---

## ❓ Troubleshooting Quick Reference

| Problem                        | Likely Cause                     | Fix                                              |
|-------------------------------|----------------------------------|--------------------------------------------------|
| Nothing on screen at boot      | Autostart not configured         | Check `~/.config/autostart/bee-kiosk.desktop`    |
| Video stuttering               | High CPU / no GPU memory         | Check `gpu_mem=256` in `/boot/firmware/config.txt` |
| Camera not detected            | Wrong device or unplugged        | Run `ls /dev/video*` — reconnect camera          |
| Screen goes blank              | Screensaver active               | Check LXDE autostart has `@xset` lines           |
| Ctrl+Shift+Q not working       | xbindkeys not running            | Run `xbindkeys` in terminal, check `~/.xbindkeysrc` |
| Triggers too often             | Sensitivity too high             | Increase `MOG2_THRESHOLD` and `MIN_AREA` in config |
| Not detecting at distance      | Sensitivity too low              | Decrease `MOG2_THRESHOLD` and `MIN_AREA` in config |

---

## 📋 Project Status

**Version:** v1.0-stable
**Date:** April 2026

**Working Features:**
- Continuous idle loop playback
- Random reaction video on motion detection
- Left / Centre / Right zone detection
- 2-frame motion confirmation (false trigger protection)
- Debug mode with live camera overlay and zone lines
- Automatic launch on Pi boot
- Clean shutdown via Ctrl+Shift+Q, desktop icon, or SSH
- Boot log at `~/bee_kiosk.log`
- Desktop icons for Run / Debug / Stop
- Screensaver permanently disabled

---

*Built by Lowell Smidteboom*
