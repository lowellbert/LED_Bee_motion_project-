#!/bin/bash
set -e
cd ~/projects/LED_Bee_motion_project-
source .venv/bin/activate
export DISPLAY=:0
export XAUTHORITY=/home/beedisplay/.Xauthority

# Run at nice +10 so VLC and system processes always get CPU priority
# over the OpenCV grabber thread
exec nice -n 10 python3 -u bee_system.py "$@"
