# Bench camera fixture

`synthetic-r1.avi` is a clip of 24 frames at 240 frames per second, 200 x 150 pixels, with the lossless FFV1 codec of `opencv-python-headless` 5.0.0.93. `python/tests/bench_camera_render.py` wrote it:

```sh
python -I python/tests/bench_camera_render.py python/tests/fixtures/bench_camera
```

A 3 mm white dot on a black field moves at 50 mm/s in +x on a Wacom-like background. Frame 9 repeats frame 8, frame 15 has no dot, and the clock LED is on in frames 0 to 11.

`synthetic-r1.frames.json` is the output of ffprobe 8.1.2 (Homebrew, macOS arm64) on this clip, made on 4 November 2026 with this command:

```sh
ffprobe -v error -select_streams v:0 -show_frames -of json synthetic-r1.avi > synthetic-r1.frames.json
```

The Homebrew ffprobe ran with `DYLD_LIBRARY_PATH=/opt/homebrew/Cellar/x265/4.2/lib`, because its link to the x265 library was broken on that machine.

The tests never run ffprobe. If you write the clip again, run the command again and keep both files together.
