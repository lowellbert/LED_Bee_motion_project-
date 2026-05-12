#!/bin/bash
# ============================================
#  Bee Motion Video System — Pi Setup Script
#  Run once after fresh Pi OS Bookworm install
# ============================================
set -e

PROJECT_DIR=~/projects/LED_Bee_motion_project-
VENV_DIR="$PROJECT_DIR/.venv"

echo "[SETUP] Installing system packages..."
sudo apt update
sudo apt install vlc libvlc-dev python3-opencv -y

echo "[SETUP] Creating virtual environment with system site packages..."
# --system-site-packages is CRITICAL — allows venv to see:
#   - picamera2 (system only)
#   - simplejpeg (system only)
#   - numpy 1.24.2 (system only — do NOT pip install numpy)
python3 -m venv --system-site-packages "$VENV_DIR"

echo "[SETUP] Installing Python packages into venv..."
source "$VENV_DIR/bin/activate"
pip install python-vlc pynput

echo ""
echo "[SETUP] ⚠️  WARNING: Do NOT pip install numpy inside the venv"
echo "         picamera2/simplejpeg require system numpy (1.24.2)"
echo "         Binary mismatch = segfault at launch"
echo ""
echo "[SETUP] ✅ Done. Launch with: bash run.sh"
