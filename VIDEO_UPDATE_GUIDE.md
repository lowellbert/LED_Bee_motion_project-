# 🐝 Bee Kiosk — Video Update Guide

---

## Overview

The Bee Kiosk plays three video files:

| Filename       | Purpose                                      |
|----------------|----------------------------------------------|
| `idle.mp4`     | Loops continuously when no motion is detected |
| `react_1.mp4`  | Plays when motion is detected (random pick)  |
| `react_2.mp4`  | Plays when motion is detected (random pick)  |

You can replace any or all of these videos at any time.
**Keep the filenames exactly the same.**

---

## Step 1 — Prepare Your Video File

### Accepted Format
- **File type:** `.mp4` only
- **Codec:** H.264
- **Resolution:** 1920x1080 (1080p) recommended
- **Frame rate:** 30fps recommended
- **Bitrate:** Under 5 Mbps recommended

---

### Converting From .MOV or Other Formats

If your video is not already an `.mp4`, convert it first using **one of these methods:**

---

#### Option A — FFmpeg (Free, Command Line — Mac/Linux/Pi)

Install FFmpeg if needed:
- **Mac:** `brew install ffmpeg`
- **Pi/Linux:** `sudo apt install ffmpeg -y`
- **Windows:** Download from https://ffmpeg.org/download.html

Convert your file:
```
ffmpeg -i your_video.mov -c:v libx264 -vf "scale=1920:1080" -crf 23 -preset fast -pix_fmt yuv420p -c:a aac -b:a 128k output.mp4
```

Then rename `output.mp4` to the correct filename:
- `idle.mp4`
- `react_1.mp4`
- `react_2.mp4`

---

#### Option B — HandBrake (Free, Easy GUI — Windows/Mac)

1. Download from **https://handbrake.fr**
2. Open your video file in HandBrake
3. Select preset: **Fast 1080p30**
4. Set output format to **MP4**
5. Click **Start Encode**
6. Rename the output file to `idle.mp4`, `react_1.mp4`, or `react_2.mp4`

---

#### Option C — Online Converter (No Install — Small Files Only)

1. Go to **https://cloudconvert.com/mov-to-mp4**
2. Upload your file
3. Download the converted `.mp4`
4. Rename to the correct filename

---

## Step 2 — Copy the Video to the Pi

### Option A — USB Drive
1. Copy your renamed `.mp4` file to a USB drive
2. Plug the USB drive into the Raspberry Pi
3. Open the file manager on the Pi desktop
4. Copy the file from the USB drive to:
```
/home/beedisplay/Desktop/video_drop/
```

### Option B — Network (Same WiFi)
If the Pi is on the same network, you can transfer via SCP from your computer:

**Mac/Linux:**
```
scp react_1.mp4 beedisplay@<PI_IP_ADDRESS>:/home/beedisplay/Desktop/video_drop/
```

**Windows (using WinSCP or FileZilla):**
- Host: `<PI_IP_ADDRESS>`
- Username: `beedisplay`
- Password: *(ask your technician)*
- Navigate to: `/home/beedisplay/Desktop/video_drop/`
- Drop your file in

> To find the Pi's IP address, open a terminal on the Pi and type: `hostname -I`

---

## Step 3 — Update the Kiosk

Once your file is in the `video_drop` folder:

1. On the Pi desktop, double-click the icon:
   **🔄 Bee - Update Videos**

2. A terminal window will open and show the progress:
```
[UPDATE] Found valid file: react_1.mp4
[UPDATE] Stopping kiosk for video swap...
[UPDATE] Swapped: react_1.mp4
[UPDATE] Restarting kiosk...
[UPDATE] Done — kiosk restarted with new videos
```

3. The kiosk will restart automatically and play the new video.

> The system also watches the `video_drop` folder automatically —
> if you leave a file there, it will update on its own within ~10 seconds.

---

## Step 4 — Verify It's Working

Watch the screen — the new video should start playing within 15 seconds.

If something looks wrong:
- Check the filename is exactly correct (lowercase, `.mp4` extension)
- Make sure the file finished copying before triggering the update
- Use the **🔧 Bee System - Debug** icon on the desktop for more info

---

## Backup

Every time a video is updated, the old version is automatically backed up to:
```
/home/beedisplay/projects/LED_Bee_motion_project-/videos/old/
```

---

## Desktop Icons Summary

| Icon | Purpose |
|------|---------|
| 📁 `video_drop` | Drop new video files here |
| 🔄 Bee - Update Videos | Run after dropping new files |
| ▶️ Bee System - Run | Start the kiosk manually |
| ⏹️ Bee System - Stop | Stop the kiosk |
| 🔧 Bee System - Debug | Run with debug overlay |

---

## Quick Reference — File Naming

| Replace this video | Use this filename |
|--------------------|-------------------|
| Loop / idle video  | `idle.mp4`        |
| Reaction video 1   | `react_1.mp4`     |
| Reaction video 2   | `react_2.mp4`     |

**Always keep filenames lowercase and exact.**

---

*For technical support contact your AV technician.*
