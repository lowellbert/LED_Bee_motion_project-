#!/bin/bash
# ============================================
#  Bee Kiosk — Video Drop Folder Watcher
#  Runs in background, triggers update on new file
# ============================================
# ── Instance guard — only one watcher allowed ──────────────────────
if pgrep -f "watch_drop.sh" | grep -v $$ > /dev/null; then
    echo "[WATCHER] Already running — exiting duplicate"
    exit 0
fi

DROP_DIR=/home/beedisplay/Desktop/video_drop
UPDATE_SCRIPT=/home/beedisplay/projects/LED_Bee_motion_project-/update_videos.sh
LOG=/home/beedisplay/bee_kiosk.log

echo "[WATCHER] $(date) — Watching $DROP_DIR for new videos..." | tee -a "$LOG"

while true; do
    # Wait for a file to be closed (finished copying) in drop folder
    inotifywait -e close_write "$DROP_DIR" 2>/dev/null
    echo "[WATCHER] New file detected in drop folder" | tee -a "$LOG"
    sleep 5  # Wait 5s to ensure copy is fully complete
    bash "$UPDATE_SCRIPT"
done
