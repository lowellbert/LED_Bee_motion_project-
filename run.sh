#!/bin/bash
set -e
cd ~/projects/LED_Bee_motion_project-
source .venv/bin/activate
export DISPLAY=:0
export XAUTHORITY=/home/beedisplay/.Xauthority
exec python3 -u bee_system.py "$@"
