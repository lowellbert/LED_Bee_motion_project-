#!/bin/bash
set -e
cd ~/projects/LED_Bee_motion_project-
source .venv/bin/activate
export DISPLAY=:0
export XAUTHORITY=/home/beedisplay/.Xauthority

# ── Instance guard ─────────────────────────────────────────────────────
EXISTING=$(pgrep -f "python3.*bee_system.py" || true)
if [ -n "$EXISTING" ]; then
    echo "[RUN] Stopping existing instance (PID $EXISTING)..."
    kill "$EXISTING"
    sleep 2
fi

# ── Verify videos exist in videos/ folder ─────────────────────────────
VIDEO_DIR=/home/beedisplay/projects/LED_Bee_motion_project-/videos
REQUIRED=("idle.mp4" "react_1.mp4" "react_2.mp4")

echo "[RUN] Verifying videos..."
MISSING=0
for f in "${REQUIRED[@]}"; do
    if [ ! -f "$VIDEO_DIR/$f" ]; then
        echo "[ERROR] Missing video: $f"
        MISSING=1
    else
        echo "[RUN] OK: $f"
    fi
done

if [ "$MISSING" -eq 1 ]; then
    echo "[ERROR] Cannot start — place idle.mp4, react_1.mp4, react_2.mp4 in $VIDEO_DIR"
    exit 1
fi

# ── Launch ────────────────────────────────────────────────────────────
echo "[RUN] All videos verified. Starting bee_system.py..."
exec python3 -u bee_system.py "$@"
