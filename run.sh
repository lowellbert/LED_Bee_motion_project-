#!/bin/bash
set -e
cd ~/projects/LED_Bee_motion_project-
source .venv/bin/activate
export DISPLAY=:0
export XAUTHORITY=/home/beedisplay/.Xauthority

# Cache videos to RAM for zero disk I/O during playback
echo "[RUN] Caching videos to RAM..."
mkdir -p /dev/shm/bee_videos
cp ~/projects/LED_Bee_motion_project-/videos/*.mp4 /dev/shm/bee_videos/
echo "[RUN] Videos cached OK"

exec python3 -u bee_system.py "$@"
