
## Next session plan (freeze issue)
- Running via VS Code Remote SSH with DISPLAY=:0 and XAUTHORITY set.
- OpenCV windows sometimes show 1 frame then freeze; program appears to stall.
- Suspect cap.read() blocking (V4L2/OpenCV call).
- Added warmup window pumping; fixed indentation; py_compile passes.
- Next steps:
  1) Add before/after cap.read() prints to confirm stall location.
  2) If confirmed, implement FrameGrabber thread to avoid blocking read.
  3) Disable VLC fullscreen while debugging so windows aren’t hidden.
  4) Consider GStreamer pipeline if V4L2 continues to stall.
