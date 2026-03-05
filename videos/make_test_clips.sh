#!/usr/bin/env bash
set -e

# --------- SETTINGS ----------
W=1280
H=720
FPS=30
DUR=5
FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# H.264 encode settings (good for Pi playback)
ENC="-c:v libx264 -profile:v main -level 3.1 -pix_fmt yuv420p -r ${FPS} -g 30 -keyint_min 30 -sc_threshold 0 -t ${DUR} -an"

# A simple "bee" marker: yellow box with black stripe
# We'll draw it at x,y that we compute per clip using expressions.
BEE="drawbox=x=%X%:y=%Y%:w=60:h=28:color=yellow@1:t=fill,\
drawbox=x=%X%+8:y=%Y%+6:w=44:h=4:color=black@1:t=fill,\
drawbox=x=%X%+8:y=%Y%+14:w=44:h=4:color=black@1:t=fill"

# Big label + zone lines
# Also prints time so you can see if clip restarts cleanly.
base_vf () {
  local LABEL="$1"
  echo "drawtext=fontfile=${FONT}:text='${LABEL}':x=40:y=40:fontsize=48:fontcolor=white:box=1:boxcolor=0x000000AA:boxborderw=12,\
drawtext=fontfile=${FONT}:text='t=%{eif\\:t\\:d}.%{eif\\:100*(t-trunc(t))\\:d}':x=40:y=110:fontsize=28:fontcolor=white:box=1:boxcolor=0x00000088:boxborderw=8,\
drawbox=x=iw/3:y=0:w=4:h=ih:color=deepskyblue@0.8:t=fill,\
drawbox=x=2*iw/3:y=0:w=4:h=ih:color=deepskyblue@0.8:t=fill"
}

# Helper to generate a clip
make_clip () {
  local OUT="$1"
  local LABEL="$2"
  local XEXPR="$3"
  local YEXPR="$4"

  # Replace placeholders in bee draw
  local BEEVF="${BEE//%X%/${XEXPR}}"
  BEEVF="${BEEVF//%Y%/${YEXPR}}"

  ffmpeg -y -f lavfi -i "color=c=black:s=${W}x${H}:r=${FPS}:d=${DUR}" \
    -vf "$(base_vf "${LABEL}"),${BEEVF}" \
    ${ENC} "${OUT}"
  echo "Created ${OUT}"
}

echo "Generating test clips at ${W}x${H} ${FPS}fps ${DUR}s..."

# ---------------- LOOPS ----------------
# Centre loop: bee gently bobbing around centre
make_clip "loop_centre.mp4" "LOOP CENTRE" \
  "iw/2-30 + 60*sin(2*PI*t/5)" \
  "ih/2-14 + 20*sin(2*PI*t/2.5)"

# Left loop: bee hovering left third
make_clip "loop_left.mp4" "LOOP LEFT" \
  "iw/6-30 + 40*sin(2*PI*t/3)" \
  "ih/2-14 + 20*sin(2*PI*t/2.2)"

# Right loop: bee hovering right third
make_clip "loop_right.mp4" "LOOP RIGHT" \
  "5*iw/6-30 + 40*sin(2*PI*t/3)" \
  "ih/2-14 + 20*sin(2*PI*t/2.2)"

# ---------------- TRANSITIONS ----------------
# Centre -> Left: bee moves from centre to left over duration
make_clip "trans_centre_to_left.mp4" "TRANS CENTRE -> LEFT" \
  "(iw/2-30) + ((iw/6-30)-(iw/2-30))*(t/${DUR})" \
  "ih/2-14"

# Left -> Centre
make_clip "trans_left_to_centre.mp4" "TRANS LEFT -> CENTRE" \
  "(iw/6-30) + ((iw/2-30)-(iw/6-30))*(t/${DUR})" \
  "ih/2-14"

# Centre -> Right
make_clip "trans_centre_to_right.mp4" "TRANS CENTRE -> RIGHT" \
  "(iw/2-30) + ((5*iw/6-30)-(iw/2-30))*(t/${DUR})" \
  "ih/2-14"

# Right -> Centre
make_clip "trans_right_to_centre.mp4" "TRANS RIGHT -> CENTRE" \
  "(5*iw/6-30) + ((iw/2-30)-(5*iw/6-30))*(t/${DUR})" \
  "ih/2-14"

# Optional blank
ffmpeg -y -f lavfi -i "color=c=black:s=${W}x${H}:r=${FPS}:d=${DUR}" ${ENC} "blank.mp4"
echo "Created blank.mp4"

echo "Done."
ls -lh *.mp4
