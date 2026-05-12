# 🐝 LED Bee Motion Video System

Interactive video kiosk that uses a Raspberry Pi camera to detect motion 
and trigger reactive video playback via VLC.
Built for immersive exhibit installations.

---

## Hardware
- Raspberry Pi 5
- Pi Camera Module 3 Wide (imx708_wide)
- HDMI display
- Logitech K400 Plus (optional keyboard control)

---

## Video Files Required
Place in the `videos/` folder:
| File | Role |
|------|------|
| `idle.mp4` | Loops when no motion detected |
| `react_1.mp4` | Triggered on motion (random) |
| `react_2.mp4` | Triggered on motion (random) |

> Videos are cached to RAM (`/dev/shm/bee_videos/`) at launch for smooth playback.

---

## Quick Start (Raspberry Pi OS Bookworm)

### 1. One-time setup
```bash
bash setup.sh
