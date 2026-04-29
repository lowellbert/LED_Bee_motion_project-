# Bee Motion Video Kiosk

An interactive, motion-reactive video display system built on Raspberry Pi.
The system watches a space with a camera, detects when someone walks by,
and responds by playing a video - automatically, unattended, and silently.

---

## What It Does

The kiosk runs in two states:

**When nobody is around:**
The system plays a looping animation (idle.mp4) continuously on the screen.
It runs on its own - no interaction needed.

**When someone walks by:**
The camera detects motion and the system instantly switches to one of two
reaction videos (react_1.mp4 or react_2.mp4), chosen at random.
Once the reaction video finishes, the system returns to the idle loop
and waits for the next visitor.

**Zone Detection:**
The camera view is divided into three zones - Left, Centre, and Right.
The system detects which zone the motion occurs in, allowing for future
zone-specific responses if needed.

**False Trigger Protection:**
Motion must be detected in two consecutive checks before a reaction triggers,
preventing lights, shadows, or camera noise from causing unwanted playbacks.

---

## How It Works

```
Power On
   |
   v
Idle video loops on screen
   |
   v
Camera watches for motion
   |
   +-- No motion detected ----------------------> Keep looping idle video
   |
   +-- Motion detected (2 confirmations)
          |
          v
       Random reaction video plays (react_1 or react_2)
          |
          v
       Reaction ends -> Return to idle loop
```

---

## Hardware

| Component    | Details                                                           |
|--------------|-------------------------------------------------------------------|
| Computer     | Raspberry Pi 5 (4GB RAM)                                          |
| Cooling      | Official Raspberry Pi Active Cooler - PWM fan, spring-loaded      |
| Camera       | Raspberry Pi Camera Module 3 Wide - 120 FOV, 12MP Sony IMX708    |
| Display      | Any HDMI monitor or TV                                            |
| Storage      | MicroSD card (64GB recommended)                                   |
| Power        | Official Raspberry Pi 27W USB-C Power Supply                      |
| Keyboard     | USB keyboard (for setup and exit shortcut)                        |

###  Raspberry Pi 5 Performance

| Metric         | Pi 4 (1GB) - old | Pi 5 (4GB) - current |
|----------------|------------------|----------------------|
| CPU idle       | 27%              | 50%                  |
| Temperature    | 67C              | 48C                  |
| Load average   | 5.7              | 1.16                 |
| Grabber thread | Spinning 80+/s   | Sleeping             |
| nice workaround| Required         | Not needed           |

###  Camera Module 3 Wide

| Feature       | Camera Module 3 Wide (current)  |
|---------------|---------------------------------|
| Interface     | MIPI CSI - native Pi pipeline   |
| Min FPS       | Fully configurable              |
| CPU cost      | Low - ISP hardware pipeline     |
| Field of view | 120 degrees wide angle          |
| Resolution    | 1536x864 at 30fps               |

> Note: The Camera Module 3 Wide (imx708_wide) outputs frames in RGB
> channel order despite the BGR888 format label. The system applies a
> COLOR_RGB2BGR correction automatically so colours display correctly.

---

## Video Files

All video files live in the videos/ folder inside the project:

| File               | Role                                            |
|--------------------|-------------------------------------------------|
| videos/idle.mp4    | Plays on loop when no one is present            |
| videos/react_1.mp4 | Reaction video - triggered randomly on motion   |
| videos/react_2.mp4 | Reaction video - triggered randomly on motion   |

**Requirements:**
- Format: H.264 / MP4 (required for hardware decode on Pi 5)
- Resolution: Any - the display will show the video fullscreen
- Audio: Not used - audio is disabled for performance
- Length: Any - idle loops forever, reaction plays once then returns

> To swap in new videos, replace the files in the videos/ folder
> keeping the same filenames, then restart the system.

---

## Daily Operation

### Starting the System

The kiosk starts automatically when the Pi powers on.
Allow approximately 10-15 seconds after the desktop appears.

To start manually:
- Double-click the BeeSystem Run icon on the desktop
- Or from a terminal: ./run.sh

### Stopping the System

| Method           | How                                          |
|------------------|----------------------------------------------|
| ESC key          | Press ESC on any attached keyboard           |
| Desktop icon     | Double-click BeeSystem Stop on desktop       |
| Remote SSH       | Run: pkill -f bee_system.py                  |

> The keyboard exit listener uses evdev (kernel-level input).
> It works even when VLC is fullscreen and has input focus.

### Debug Mode

Debug mode shows a live camera feed with motion detection overlays.
Useful for checking camera alignment and testing detection sensitivity.

To launch in debug mode:
- Double-click BeeSystem Debug on the desktop
- Or from a terminal: ./run.sh --debug

While in debug mode:
- Press D to toggle the camera overlay on or off
- Press Q or ESC to exit

### Checking the System Log

```bash
tail -50 ~/bee_kiosk.log
```

A healthy log looks like:

```
[FrameGrabber] Starting picamera2 at 1536x864 30fps
[FrameGrabber] picamera2 started OK
[INIT] Camera ready in 0.75s - OK
[BeePlayer] -> IDLE loop
[BeePlayer] -> REACTING (react_1.mp4)
[BeePlayer] Reaction ended -> returning to IDLE
[HEARTBEAT] frames=300 cam_age=0.06s player=IDLE debug=OFF
```

---

## Installation and Setup

For full step-by-step setup instructions see SETUP.md.

**Summary of setup steps:**

1. Flash Raspberry Pi OS with Desktop (Bookworm, 64-bit) to MicroSD
2. Set hostname raspberrypi and username beedisplay in Raspberry Pi Imager
3. Assemble the Pi 5 active cooler and Camera Module 3 before first boot
4. Boot the Pi and install system dependencies
5. Clone this repository into ~/projects/LED_Bee_motion_project-
6. Create the Python virtual environment and install requirements
7. Restore config files for autostart, screensaver disable, and exit hotkey
8. Copy desktop icons to ~/Desktop and mark as trusted
9. Reboot - the system launches automatically

> Pi 5 Note: gpu_mem split is NOT required on Pi 5. The Pi 5 manages
> GPU/CPU memory automatically. VLC hardware decode works via v4l2m2m
> without any manual configuration.

---

## Adjusting Motion Sensitivity

Open bee_system.py and find the CONFIG section near the top.

| Setting           | What it controls                      | Current Value |
|-------------------|---------------------------------------|---------------|
| MOG2_THRESHOLD    | How sensitive the motion detection is | 25            |
| MIN_AREA          | Minimum size of motion to trigger     | 300           |
| MOTION_COOLDOWN   | Seconds to wait between triggers      | 2.0           |

**Too many false triggers:**
- Increase MOG2_THRESHOLD to 35 or higher
- Increase MIN_AREA to 500 or higher
- Increase MOTION_COOLDOWN to 3.0 or higher

**Not detecting people at distance:**
- Decrease MOG2_THRESHOLD to 15
- Decrease MIN_AREA to 150

After changes, save the file and restart the system.

---

## Technical Stack

| Component        | Technology                                                  |
|------------------|-------------------------------------------------------------|
| Language         | Python 3.11                                                 |
| Motion Detection | OpenCV MOG2 background subtraction (greyscale, 154x87px)   |
| Video Playback   | VLC via python-vlc (hardware H.264 decode via v4l2m2m)      |
| Camera Capture   | picamera2 / libcamera - native MIPI CSI at 1536x864 30fps  |
| Colour Fix       | RGB to BGR channel swap (imx708_wide quirk correction)      |
| Threading        | FrameGrabber thread + main loop + VLC internal thread       |
| Keyboard Exit    | evdev kernel-level listener - bypasses X11 and VLC focus    |

**Key Files:**

| File                  | Purpose                                      |
|-----------------------|----------------------------------------------|
| bee_system.py         | Main application - all logic lives here      |
| run.sh                | Production launcher                          |
| requirements.txt      | Python package dependencies                  |
| SETUP.md              | Full setup and recovery playbook             |
| videos/               | Folder containing all video files            |
| BeeSystem-*.desktop   | Desktop launcher icons                       |
| bee-kiosk.desktop     | Autostart entry for boot launch              |

**Git:**
- Branch: main
- Stable tag: v2.0-pi5-stable
- Pi 4 backup: bee_system_pi4_backup.py (kept for reference)

---

## Troubleshooting Quick Reference

| Problem                       | Likely Cause                   | Fix                                                      |
|-------------------------------|--------------------------------|----------------------------------------------------------|
| Nothing on screen at boot     | Autostart not configured       | Check ~/.config/autostart/bee-kiosk.desktop              |
| Video stuttering              | High CPU load                  | Run top - check no VS Code node process running          |
| Camera not detected           | Cable not seated               | Reseat CSI cable - Pi 5 uses smaller connector than Pi 4 |
| Camera image wrong colour     | RGB/BGR channel order quirk    | Confirm _correct_colour() is called in _run()            |
| Screen goes blank             | Screensaver active             | Check LXDE autostart has xset lines                      |
| ESC not working               | evdev keyboard not found       | Run: python3 -c "import evdev; print(evdev.list_devices())" |
| Desktop icon asks to execute  | pcmanfm quick_exec not set     | Set quick_exec=1 in ~/.config/pcmanfm/default/pcmanfm.conf |
| Triggers too often            | Sensitivity too high           | Increase MOG2_THRESHOLD and MIN_AREA in config           |
| Not detecting at distance     | Sensitivity too low            | Decrease MOG2_THRESHOLD and MIN_AREA in config           |


---

## Project Status

**Version:** v2.0-pi5-stable
**Date:** April 2026

**Working Features:**
- Continuous idle loop playback
- Random reaction video on motion detection
- Left / Centre / Right zone detection
- 2-frame motion confirmation (false trigger protection)
- picamera2 native capture with RGB to BGR colour correction
- Debug mode with live camera overlay and zone lines
- ESC and Ctrl+Alt+Q keyboard exit (kernel-level, works in kiosk mode)
- Automatic launch on Pi boot
- Clean shutdown via ESC, desktop icon, or SSH
- Boot log at ~/bee_kiosk.log
- Desktop icons for Run / Debug / Stop
- Screensaver permanently disabled
- Tested stable: 48C, load avg 1.16, throttle 0x0

---

*Built by Lowell Smidteboom*
