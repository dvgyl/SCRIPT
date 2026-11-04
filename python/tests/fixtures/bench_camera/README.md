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

`synthetic-r1-turned.mov` holds the first 3 frames of `synthetic-r1.avi`, copied without a new encoding into a QuickTime file with a display matrix of a half turn. A phone can write this matrix for a clip that it records upside down. The ffmpeg 8.1.2 of the same machine made it on 4 November 2026, with the same library path, with this command:

```sh
ffmpeg -display_rotation 180 -i synthetic-r1.avi -frames:v 3 -c copy synthetic-r1-turned.mov
```

The tests never run ffmpeg or ffprobe. If you write the clip again, run the commands again and keep the files together.
