# Bench protocol

Version 1.2. The status is draft until the lock (section 14). After the lock, every change is a deviation (section 13). Version 1.1 revised version 1.0 after its independent review. Then sections 2.4 and 5.2 were corrected to match the capture tools: the clip wording, the rotation channel, and the time of a mark.

Version 1.2 revises version 1.1 after the build of the analysis software, before the lock. It adds the start point to the move list (sections 5.1 and 15). It changes the ramp tolerance (section 7.4), the LED rule and the readout clip (section 7.3), and the path fits (sections 7.6 and 8.2). It gives each program set its own correction model, fitted on the raw device values (sections 11.5, 11.8, and 12). Section 15 names the analysis commands and their files. Section 4.3 now holds the checked facts of the NextDraw 8511.

This protocol defines the robotic bench study of `docs/PAPER_PLAN.md`. A motion system moves a stylus along registered programs on two devices. A camera records the executed movement of every run. The study has no participants and uses no data from people. It makes no mental health claim and no claim about people.

A reader with the hardware can run the study from this document. Sections 2 to 7 cover the devices, the fixture, the motion system, the programs, the mountings and the run order, and the camera. Sections 8 to 12 cover the measures, the features, the equivalence limits, the statistics, and the deliverables for the consequence experiment. Sections 13 to 16 cover the stop rules and deviations, the lock, the file layout, and the claims that the paper may make.

Three scripts belong to this protocol. `python/studies/bench_programs.py` computes the geometry, the timing, and the motion demands of every program and writes the move lists and the run orders. `python/studies/bench_limits.py` computes the equivalence limits from the source values of section 10. `python/studies/bench_precision.py` simulates the precision of the design. Section 11.8 gives their commands and seeds. The analysis code is the package `scriptkin.bench` (section 2.3).

## 0. Terms

| Term | Meaning |
| --- | --- |
| Device P | The iPad Pro with the Apple Pencil Pro and SCRIPT Capture in bench mode. |
| Device W | The Wacom Intuos Pro Medium (2025) with the Pro Pen 3, a Mac, and SCRIPT Bench. |
| Program | A registered list of timed moves that the motion system executes (section 5). |
| Run | One execution of one program on one device. One run is one trial in one journal. |
| Mounting | One placement of a device in the fixture, with the code `M1` to `M8`. Section 6.1 defines a mounting change. |
| Block | All runs of one mounting. |
| Journal | A SCRIPT recording in the format of `docs/DESIGN.md` section 3. |
| Contact segment | A part of a contact stroke that `scriptkin.quality.assess` accepts. |
| Motion interval | The part of a run from the motion start to the motion end of section 7.4. One rule finds it on both devices and on the camera track. |
| Comparison scale | The scale on which a feature is compared, either the natural logarithm of the value or the value itself (section 11.1). |
| Camera-referenced difference | For one run, the device value minus the camera value of the same feature, on the comparison scale (section 9.2). |
| Mounting mean | The mean of the valid runs of one program in one mounting, on the comparison scale. |
| Bias | The mean of the device P mounting means minus the mean of the device W mounting means, for one feature and one program. |
| Feature set | The six features of section 9.1. Their decisions are the claims. |
| Program set | The programs on which a feature is tested (section 9.1). |
| Equivalence limit L | The registered bound on the bias of one feature (section 10). |
| Camera track | The marker positions of one run from the camera, in millimetres in the marker plane. |
| Lock | The registration of this protocol, the programs, the limits, the analysis code, the capture builds, and the constants before the main runs. |
| Deviation | Any departure from this protocol after the lock. |

## 1. Purpose and claim

The bench tests whether the SCRIPT measurement chain gives the same feature values on device P and on device W for the same movement. The motion system commands the same movement on both devices. The camera measures the movement that each run executes.

The claim is the claim of `docs/PAPER_PLAN.md` section 2.2. For each feature in the feature set, the two chains give equivalent values within the limit of section 10, or they do not. The decision covers every program of the program set of the feature. For the four features that the camera measures, the comparison is referenced to the camera track of each run. It then holds for the executed movement. For the other two features, the comparison holds for the commanded movement, executed within the camera check of section 7.6. Where a feature is not equivalent, a correction learned on the bench reduces the difference to a stated size. A lack-of-fit check on held-out mountings tests the correction model. The claim covers only the tested units, software versions, surfaces, mountings, stylus angle and azimuth, load, region of the active area, and speeds.

The bench answers these questions:

1. Agreement. For each feature, is the bias within the limit for every program of its set, with mounting effects in its confidence interval?
2. Correction. After the correction, is the residual bias on held-out mountings within the same limit for every program?
3. Sampling. What are the sample interval, its spread, the gap share, and the noise floor of each device during the programs?
4. Accuracy. How far are the device positions from the camera track and from the commanded path?
5. Quality gate. Does each fault program give the expected quality finding on each device?
6. Consequence inputs. What bias and noise values does the consequence experiment of the plan receive (section 12)?

## 2. Devices and software

Record every item of this section in `bench/registry.json` (section 15) before the lock.

### 2.1 Device P

- An iPad Pro (M4 or M5) with an Apple Pencil Pro. Record the model identifier, the iPadOS version, and the Pencil model.
- SCRIPT Capture in bench mode (section 2.4). Record `app.version`, `app.build`, and `app.sourceRevision` from the journal.
- The scale source is the model table: `mmPerPoint = 25.4 × nativeScale / ppi` (`docs/DESIGN.md` section 3.3). Record the values.
- No screen protector. The journal records `surface.protector` as `none`.
- A new standard Pencil tip at the start of the study, and a planned replacement at the start of mounting 4 (section 6.1). Record `surface.nib`. Inspect the tip at the start of each block. A tip that shows wear outside the plan is a deviation.
- Turn the squeeze action of the Pencil Pro off in Settings, so the holder clamp cannot trigger it. Record the setting.
- Low Power Mode off. Battery above 50 percent at the start of each block. No other app in the foreground. Notifications off.

### 2.2 Device W

The tablet is a Wacom Intuos Pro Medium (2025), model PTK670, with the Wacom Pro Pen 3. These values come from the Wacom product page (reference W1):

| Item | Value on the Wacom page |
| --- | --- |
| Active area | 263 × 148 mm |
| Pen resolution | 5080 lpi. This is 200 lines per millimetre, or 0.005 mm per line. |
| Pen pressure levels | 8192 |
| Pen tilt | ± 60 degrees |
| Pen technology | Electromagnetic resonance |
| Connection | USB cable or Bluetooth |

The Wacom page states a "high report rate" and gives no number. A retailer lists 300 reports per second. This protocol does not use that number. The pilot measures the report rate as the median sample interval of the contact strokes (section 8.1). The Wacom page gives no accuracy value for this model.

- Connect the tablet with the USB cable, not with Bluetooth.
- Use the standard nib. Fit a new nib at the start of the study and at the start of mounting 4 (section 6.1). Record every replacement.
- Record the Wacom driver version, the macOS version, the Mac model, and the SCRIPT Bench version.
- Turn off every driver feature that changes the pen reports. Record the driver settings.
- Disable sleep and the display sleep on the Mac for the whole block. Record the setting.
- The Mac device checks of `docs/DEVICE_TESTING.md` must pass before the pilot (section 13). They cover the `absoluteX` and `absoluteY` values and their direction, the pressure maximum, the clock check, and the report rate.

### 2.3 Analysis software

- `scriptkin` at the locked commit (section 14), with the Python version and `python/uv.lock`.
- The bench package `scriptkin.bench`. `scriptkin.bench.moves` reads the move lists (section 5.1). `scriptkin.bench.features` holds the motion interval and the features (sections 7.4, 8.1, and 9). `scriptkin.bench.camera` holds the camera record checks and the spatial comparisons (sections 7.3 to 7.7, 8.2, and 9.2). `scriptkin.bench.stats` holds the statistics (sections 11 and 12).
- The camera steps of section 7 in `scriptkin.bench.calibration`, `scriptkin.bench.clock`, and `scriptkin.bench.tracker`. They need OpenCV, which the `camera` extra of `scriptkin` installs as `opencv-python-headless`. `ffprobe` writes the frames file of each clip (section 15). Record both versions.
- The quality policy is `scriptkin.quality.DEFAULT_QC`: `max_gap_s` 0.05, `min_segment_s` 0.1, `min_rate_hz` 40, `reject_unresolved` true.
- The preprocessing is `scriptkin.signal.DEFAULT_PREPROCESSING`: `grid_hz` 200, Savitzky-Golay `window` 11 and `polynomial` 3, `welch_window_s` 2, `band_hz` 4 to 12.

### 2.4 Capture software requirements

The build phase of the plan delivers two capture tools. Both write journal format 2.0 (`docs/DESIGN.md` section 3). The design is authoritative for every line shape. This section lists only what the bench needs from it.

Both tools:

- `sessionOpened.purpose` is `bench`. The header has no participant keys. Its `bench` object holds the device role (`P` or `W`) and the mounting code (`M1` to `M8`). It also holds the motion system id and version, and the protocol version and SHA-256.
- One journal holds one mounting of one device, which is one block (section 6.3).
- Each run is one trial of a bench task of `contract/battery.json`: `bench-loops-l`, `bench-loops-s`, `bench-spiral-l`, `bench-spiral-s`, or `bench-line` (section 5.1). `trialStarted.spec` holds the task specification, so the journal is self-contained.
- `trialStarted.bench` holds the program id, the run index, the trajectory digest, and the clip name. The run index is 1 to 5 for the planned runs and 6 or 7 for the reruns of section 6.4. The trajectory digest is the SHA-256 of the controller file that the motion system executes (section 4.1). The tool copies it from the bundled programs file `contract/bench_programs.json`, so the operator never types it. The clip name is the run id of section 15. The tool writes it, and the run screen shows it before Begin. The operator names the camera file with that run id.
- The operator taps Begin before the stylus lowers and Done after the stylus lifts. Done ends the trial with reason `operatorDone`.
- The operator adds a `disturbance` or `deviceProblem` mark before Done. The tools accept a mark only on the open run.
- The operator never marks a fault run as excluded. The quality gate of section 5.3 needs the fault runs as ordinary trials.
- The session refuses every questionnaire, safety, and handoff line.
- No run writes a synchronisation mark. The camera and the devices are aligned in the analysis (section 7.4).

SCRIPT Capture bench mode on device P:

- Every sample, correction, hover, and finger line is written as in a study session.
- The stimulus origin is the origin of the task view. The bench analysis aligns the drawn path to the commanded path by its digest, so the stimulus origin is not a reference.

SCRIPT Bench on the Mac for device W:

- It reads the tablet reports through `NSEvent` with mouse event coalescing off.
- `sessionOpened.input` is `tablet`. The `tablet` object records the tablet model, its asset code, the driver version, and the pen.
- Each report gives a `sample` line. `x` and `y` are tablet lines from `absoluteX` and `absoluteY`, with the origin at the top left and y down. `force` is the normalised pressure. `tiltX` and `tiltY` are the native tilt values. `est` is 0.
- `scale` is `{"kind": "tablet", "linesPerMM": 200, "mmPerPoint": 0.005}`, from the pen resolution in section 2.2.
- `channels` plans `force`, `tiltX`, `tiltY`, and `hover`. The plan of the Pro Pen 3 has no `rotation` channel, so the journal records no rotation.
- A pen report in proximity without contact gives a `hover` line without height.
- The Set origin action stores the hover position of the pen as `stimulusOrigin`. Section 6.1 uses it at every mounting.
- The window must keep the keyboard focus. Loss of focus during a trial ends the trial with reason `backgrounded`.

The programs file `contract/bench_programs.json` is generated by `scriptkin contract regenerate` from the module `scriptkin.bench`. The module reads the protocol file digest and the digest of each controller file under `bench/programs/`. Both capture tools bundle the file. The lock hashes it (section 14). The analysis refuses a journal whose `app.build` or `app.sourceRevision` is not in the lock file.

Before the lock, a parity test must pass. Synthetic raw events for both tools give journals whose `scriptkin` feature values equal the values of the equivalent synthetic recording within 1e-9.

## 3. Fixture and stylus holding

### 3.1 Fixture

- A rigid base plate carries the device and the motion system. The camera mount is fixed to the same plate.
- The device lies flat on the plate, held by edge clamps that touch no active area and no button.
- Level the device surface within 0.5 degrees. Check with a bubble level at the start of each block.
- Align the device axes with the motion axes within 1 degree. Section 6.2 gives the check.
- Device P has bare glass. Device W has its bare tablet surface. Clean both with a dry microfibre cloth before each block.

### 3.2 Stylus holder

- The holder keeps the stylus at an altitude of 60 degrees above the surface. The barrel points in the negative x direction of the device. The altitude and the azimuth are constant in a run.
- A free vertical slide carries the stylus. Only the weight of the stylus, the slide, and the added mass presses on the surface.
- The total vertical load is 100 g, measured with a scale with 0.1 g resolution. Adjust the added mass so that the load is equal on both devices within 1 g. Record both values.
- The holder must not touch the device surface. The holder is non-conductive near the device P screen, or the pilot shows that the holder gives no finger lines in the device P journals.
- The clamp holds the Pencil Pro barrel away from the squeeze area (section 2.1). The clamp holds the Pro Pen 3 barrel away from its side switches.
- The camera marker is a 3 mm matte white dot on a black field. It is fixed to the stylus barrel, as near to the nib as the camera view permits (section 7.1). Measure its height z_m above the surface with a caliper and record it.
- Measure the altitude with a digital angle gauge at the start of each block. If the altitude differs from 60 degrees by more than 1 degree, adjust the holder before the block.

Friction and stiffness, once before the pilot and after any change of the holder:

1. Friction. Pull the loaded stylus across each device surface at about 20 mm/s with a force gauge with 0.01 N resolution. Pull along the barrel direction and across it. Record the mean friction force per surface and direction.
2. Stiffness. Put the stylus on the surface. Apply a lateral force of 2 N at the nib with the force gauge, in x and in y. Measure the nib displacement with a dial indicator with 0.01 mm resolution. The stiffness is the force divided by the displacement. Record both directions.
3. Deflection. The nib deflection under friction is the friction force divided by the stiffness. It must be below 0.02 mm on both surfaces and in both directions. If it is not, stiffen the holder before the pilot.

The angle and the load are fixed because the devices convert contact force to pressure in different ways. The bench does not calibrate force. Pressure features are descriptive only (section 9.3).

## 4. Motion system

### 4.1 Requirements

Any motion system for this bench must meet every requirement in this table. `bench_programs.py` computes the demands of the programs (section 5.1).

| Requirement | Value | Reason |
| --- | --- | --- |
| Travel | At least 152 × 124 mm | The largest frame is 132 × 104 mm, plus 10 mm on each side for the approach. |
| Position resolution | 0.025 mm per step or finer | Four steps per 0.1 mm of path, so that step quantisation stays below the device noise. |
| Repeatability | 0.1 mm or better at the program speeds | The executed movement check allows 0.2 mm (section 7.6). |
| Speed range | 5 to 110 mm/s | The programs command 6.8 to 107.7 mm/s. Without the fast loop program, 5 to 60 mm/s. |
| Speed accuracy | Within 1 percent of the command over a 100 mm line | The camera measures it (section 4.2). |
| Acceleration | At least 3500 mm/s² with the fast loop program, else at least 2000 mm/s² | Twice the peak commanded acceleration of 1704 mm/s², or of 826 mm/s² without the fast loop. |
| Timed moves | The controller accepts a move to an end point in a given duration, or a G1 move with a feed rate | The programs are lists of timed moves. |
| Lookahead | Off, or with a known model | Corner smoothing changes the executed path. The camera check decides whether the executed path is within 0.2 mm. |
| Input shaping | Off | It changes the executed timing. |
| Stylus load and angle | Set by the holder of section 3.2, not by the machine | The load must be constant and known. |
| Stylus lift | A vertical lift of at least 10 mm, a lift to 2 mm for `fault-lift`, and a controlled lowering speed of 10 mm/s or less | A fast drop bounces the nib. |
| Device motion | The device stays still. If the device moves, the camera must also track a marker fixed to the device. | The executed movement is the stylus movement relative to the device. |
| Vibration at rest | Marker position noise of 0.02 mm or less (camera, 2 s still) | Vibration adds to the measured noise. |
| Command record | The controller file of each program is generated by a script from the registered move list and hashed | The commanded trajectory is known from the file, and the camera verifies the execution. |
| Settings record | Every motion setting is in `bench/registry.json` | A reader can repeat the execution. |

The translation script from a move list to a controller file is part of the lock. It must not change the end points, the durations, or the lifts. The SHA-256 of the controller file is the trajectory digest of the journal (section 2.4). The SHA-256 of the move list is recorded in `bench/registry.json`.

### 4.2 Qualification test

Do this test for the chosen motion system before the pilot. The camera must pass section 7.5 first. Record every result in `bench/registry.json`.

Steps 1 to 3 use a blank plate at the device position and the marker on the stylus:

1. Command the marker to 25 grid positions, 5 × 5 at 25 mm spacing. Hold 2 s at each. The root mean square difference between the camera position and the command is 0.05 mm or less.
2. Repeat step 1 five times. The root mean square spread of the five positions at each point is 0.1 mm or less.
3. Record 2 s of camera video with the marker at rest. The position noise is 0.02 mm or less on each axis.

Steps 4 to 9 run on each device surface, with the device in place and the loaded stylus on it. Do them on device P and on device W:

4. Command a 100 mm line in +x at 20 mm/s and at 40 mm/s, then the same in +y. The camera speed agrees with the command within 1 percent. The camera length agrees with 100 mm within 0.5 mm.
5. Execute `loops-s-v40` five times. Align each camera track to the commanded path (section 7.6). The largest distance is 0.2 mm or less in each run. The motion duration is within 1 percent of 6.79 s.
6. Execute `spiral-s-v40` five times with the same limits and a duration within 1 percent of 6.63 s.
7. Execute `loops-l-v80` five times with the same limits and a duration within 1 percent of 4.90 s. If this step fails on either surface, drop the program with `bench_programs.py --exclude loops-l-v80` before the lock, and record the result.
8. Lower the stylus onto the surface ten times. The camera shows no bounce. The marker position is stable within one frame after contact.
9. Put the marker at the four corners of the largest frame with the arm over the device. The camera sees the whole marker at each corner.

If any step fails, change the settings and repeat the test. If the system cannot pass, choose another candidate.

### 4.3 Candidates

NextDraw 8511 pen plotter, the successor of the AxiDraw. The NextDraw 8511 page (reference M3) gives these values. The usable travel is 300 × 218 mm. The resolution is 80 steps per mm, at 16 microsteps per full step. The reproducibility is better than 0.1 mm at low speeds. The maximum speed is 38 cm per second, which needs 8 microsteps and 40 steps per mm. At 80 steps per mm, the step limit of 25,000 steps per second allows 221 mm/s. The vertical pen travel is 17 mm, and 7.5 mm with the optional spring, so do not fit the spring. The pen lift is a brushless servo on a vertical slide. The page gives no acceleration, stiffness, or backlash value.

The controller is an EBB board with firmware 3.0.2 or later. Its `SM` command is a timed move: a step count for each motor and a duration in whole milliseconds (reference M4). Consecutive moves queue without dead time. A move of 10 ms at 107.7 mm/s uses at most 49 percent of the step limit. The NextDraw Python API has no timed move, so the translation script sends `SM` commands as raw commands. It rounds the absolute motor positions of the registered end points, which bounds the end-point error at 0.00625 mm without drift. The device stays still under the moving arm. The arm is a belt-driven gantry, so its rigidity at the stylus must pass section 4.2. The servo sets the lift height and the lowering rate, and section 4.2 measures the real lowering speed. The research notes of 4 November 2026 list what the owner must confirm on the machine.

GRBL pen plotter. A budget belt plotter with GRBL firmware accepts G1 moves with feed rates. GRBL plans junctions with a junction deviation setting, so the executed path differs from the command at corners. The frame rigidity and the repeatability vary between kits. The qualification test decides.

Klipper 3D printer, Ender-3 V3 KE. The Creality page (reference M2) gives these values. The build volume is 220 × 220 × 240 mm. The speed is 500 mm/s or less. The acceleration is 8000 mm/s² or less. The structure is Cartesian, and the firmware of the series is based on Klipper. The page does not name the moving axis. In the Ender-3 design the bed moves in y, and the device with it. The camera must then track a device marker as well as the stylus marker. The device cable and the tablet weight load the moving bed. Klipper's lookahead needs the registered settings of protocol 4.8: `SQUARE_CORNER_VELOCITY`, `MINIMUM_CRUISE_RATIO` 0, and input shaping off. The executed path model of the reference implementation (`bench_reference/src/bench_reference/motion.py`) still applies.

The recommended system is the NextDraw 8511. The device stays still, which keeps the camera method of section 7 simple. The resolution and the repeatability meet section 4.1. The open risks are the gantry rigidity, the servo lift, and the acceleration at 80 mm/s. A pause of the host computer in the stream of moves is a fourth risk. Section 4.2 tests them. The Ender-3 V3 KE is the fallback when the NextDraw fails section 4.2. It has the highest acceleration and a modelled planner, at the cost of a moving device.

## 5. Programs

### 5.1 Main programs

The main programs use three geometries. The loops and the spiral are the battery curves `loops` and `spiral-trace` of `contract/battery.json` (`scriptkin.battery`), at two scales. The line is a straight segment with a designed speed oscillation. Each geometry and scale is one bench task of the battery: `bench-loops-l` and `bench-spiral-l` at scale 1.0, `bench-loops-s` and `bench-spiral-s` at scale 0.7, and `bench-line`. Coordinates are in the stimulus frame. The origin is at the top left, x goes to the right, y goes down, and the unit is the millimetre.

The loops curve, with the parameter t from 0 to 16π:

- x(t) = 8 + 12 t / (2π) + 4 sin t
- y(t) = 24 − 10 (1 − cos t)

The spiral curve, with θ from 0 to 4π and r(θ) = 8 + 21 θ / (2π):

- x(θ) = 52 + r cos θ
- y(θ) = 52 − r sin θ

A program at scale s multiplies both coordinates by s. The bench mode shows the stimulus at the same scale, so the drawn figure and the stimulus agree.

The line runs in +x from (4, 8) to (128, 8) in a frame of 132 × 16 mm. Its cruise covers 120 mm at 20 mm/s in 6 s. The position along the line carries the oscillation A (1 − cos 2πft), so the speed is 20 + 2πfA sin 2πft mm/s. The three line programs have f of 5, 8, and 11 Hz with A of 0.4, 0.25, and 0.18 mm. Each gives a speed amplitude near 12.5 mm/s and a commanded band power near 78 (mm/s)². The cruise holds a whole number of cycles, so the oscillation starts and ends at rest.

The tangential speed profile:

- For the loops, the speed is proportional to the cube root of the curvature radius. The radius is capped at 40 mm. The constant makes the mean speed outside the ramps equal to the program speed.
- For the spiral and the line, the cruise speed is constant and equal to the program speed.
- Every program starts and ends with a ramp of 0.2 s at constant tangential acceleration. The ramp goes from rest to the speed at the start point, and back at the end. For the spiral and the line, this is the program speed. For the loops, it is the curvature speed of the start point, which is lower. At the program speed, the ramp covers 2 mm at 20 mm/s, 4 mm at 40 mm/s, and 8 mm at 80 mm/s.

The move list samples the time parametrisation every 10 ms. Each move is an end point and a duration. The last move can be shorter. The file also holds the start point of the program, because the first move needs it. `bench_programs.py --write bench/programs` writes the lists.

| Program | Task | Speed (mm/s) | Path (mm) | Duration (s) | Mean speed (mm/s) | Cruise speed (mm/s) | Speed CV | Peak acceleration (mm/s²) | Smallest radius (mm) | LDLJ | Band power (mm/s)² | Moves |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `loops-l-v20` | `bench-loops-l` | 20 | 376.7 | 19.03 | 19.8 | 6.8 to 26.9 | 0.298 | 107 | 0.44 | 16.70 | 0.011 | 1904 |
| `loops-l-v40` | `bench-loops-l` | 40 | 376.7 | 9.61 | 39.2 | 13.7 to 53.9 | 0.314 | 427 | 0.44 | 16.37 | 1.83 | 962 |
| `loops-l-v80` | `bench-loops-l` | 80 | 376.7 | 4.90 | 76.9 | 27.3 to 107.7 | 0.342 | 1704 | 0.44 | 16.35 | 90.0 | 491 |
| `loops-s-v20` | `bench-loops-s` | 20 | 263.7 | 13.38 | 19.7 | 6.8 to 26.9 | 0.305 | 153 | 0.31 | 16.46 | 0.091 | 1339 |
| `loops-s-v40` | `bench-loops-s` | 40 | 263.7 | 6.79 | 38.9 | 13.7 to 53.9 | 0.326 | 609 | 0.31 | 16.34 | 8.45 | 679 |
| `spiral-l-v20` | `bench-spiral-l` | 20 | 367.5 | 18.57 | 19.8 | 20.0 | 0.086 | 141 | 7.55 | 16.23 | 0.0001 | 1858 |
| `spiral-l-v40` | `bench-spiral-l` | 40 | 367.5 | 9.39 | 39.1 | 40.0 | 0.122 | 258 | 7.55 | 14.42 | 0.0006 | 939 |
| `spiral-s-v20` | `bench-spiral-s` | 20 | 257.2 | 13.06 | 19.7 | 20.0 | 0.103 | 122 | 5.28 | 15.27 | 0.0001 | 1307 |
| `spiral-s-v40` | `bench-spiral-s` | 40 | 257.2 | 6.63 | 38.8 | 40.0 | 0.145 | 297 | 5.28 | 13.65 | 0.0010 | 664 |
| `line-osc5` | `bench-line` | 20 | 124.0 | 6.40 | 19.4 | 7.4 to 32.6 | 0.467 | 392 | straight | 19.53 | 78.8 | 640 |
| `line-osc8` | `bench-line` | 20 | 124.0 | 6.40 | 19.4 | 7.4 to 32.6 | 0.467 | 619 | straight | 21.27 | 78.0 | 640 |
| `line-osc11` | `bench-line` | 20 | 124.0 | 6.40 | 19.4 | 7.6 to 32.4 | 0.463 | 826 | straight | 22.34 | 74.2 | 640 |

The speed CV is time-weighted on the 200 Hz grid over the whole run, ramps included. LDLJ is the commanded log dimensionless jerk of section 9.3. Band power is the commanded 4 to 12 Hz band power of the speed, computed as the bench module computes it. The ramps give the spiral programs a small speed CV and a band power below 0.001 (mm/s)².

The speed CV and the band power of the table come from the analytic path over the whole run. Two effects make the values of a run lower. First, the move list follows the oscillation with straight moves of 10 ms. On the move list, the band power of `line-osc5`, `line-osc8`, and `line-osc11` is 77.84, 75.57, and 69.92 (mm/s)². These values are 1.2, 3.1, and 5.8 percent below the table. Section 6.5 uses these move list values as the command. Second, the bench features resample a record to the 200 Hz grid by linear interpolation. This lowers the band power of a noise-free record at 240 Hz by a further 0.7 to 3.1 percent, the most at 11 Hz. The effect is part of the measurement chain under test. The bench speed CV on the motion interval is also lower than the table. The difference is 0.005 for the loops, 0.010 to 0.013 for the spiral, and 0.011 to 0.033 for the line.

Every program except `loops-l-v80` lasts 6 s or more, so one gap split still leaves four Welch windows of 2 s for the band power. `loops-l-v80` is kept only when the motion system passes section 4.2 step 7. The mean speeds of 20 and 40 mm/s are slow writing speeds. The fast loop program approaches the adult loop speed of 104.55 mm/s on paper (reference R6). The claim does not extend to faster movement.

### 5.2 Run sequence

For every run:

1. Move the stylus above the start point of the program at the lift height.
2. The operator checks the run id on the run screen and taps Begin.
3. Lower the stylus. Wait 0.5 s.
4. Execute the move list.
5. Wait 0.3 s. Lift the stylus.
6. The operator taps Done within 5 s.

The camera records from before step 3 to after step 5. The still dwells of steps 3 and 5 are outside the motion interval. Every timing and kinematic feature is computed on the motion interval only (section 7.4), so the dwells enter no feature.

### 5.3 Fault programs

Each fault program is the program `loops-l-v20` with one change after loop 4, which is the parameter 8π of 16π. `bench_programs.py --write` writes their move lists. The faults test the quality findings of `scriptkin.quality.assess` and the features that a fault must change. The operator never marks a fault run as excluded.

| Program | Change | Expected result on device P | Expected result on device W |
| --- | --- | --- | --- |
| `fault-dwell` | The stylus stops for 0.5 s on the surface, then continues. The move list holds a move of zero length and 0.5 s. | Written from the pilot before the lock (section 6.5). The pilot records whether a still Pencil under the load gives samples. The expectation is then either a `gaps` finding with two contact segments, or no finding. `bench.motion_s` is within 2 percent of the clean median plus 0.5 s. | No `gaps` finding, because the tablet reports a still pen. `bench.motion_s` is within 2 percent of the clean median plus 0.5 s. |
| `fault-lift` | The stylus rises 2 mm at 10 mm/s, stays 0.3 s, lowers at 10 mm/s, and continues. The in-air time is about 0.7 s. The move list holds a move with `lift_mm` 2. | `timing.stroke_count` is 2. | `timing.stroke_count` is 2. |
| `fault-truncated` | The program stops after loop 4. | `bench.path_length_mm` and `bench.motion_s` are at most 0.6 times the clean medians of the block. | The same. |

The clean median is the median of the valid `loops-l-v20` runs of the block. A fault run counts as detected when every expected result for its device holds. Report the detection fraction per fault and device with an exact 95 percent Clopper-Pearson interval.

One operator fault is not a program. Before the lock, on each device, send the capture tool to the background during a run once. The journal must end the trial with reason `backgrounded`, and `assess` must reject it with `incomplete_trial`. This is a software check, not a bench measure.

## 6. Mountings, run order, and blocking

### 6.1 Mountings

Each device has 6 mountings, `M1` to `M6`. Section 6.6 can add `M7` and `M8`. One mounting change means all of these steps:

1. Lift the stylus. Unclamp the device. Lift it off the plate.
2. Clean the surface. Place the device again. Shift it by at least 10 mm in x or in y from the previous mounting. Keep the centre of the largest frame within 10 mm of the centre of the active area. Keep every program point at least 10 mm from the edge of the active area. On device P the active area is the task view.
3. Clamp. Level within 0.5 degrees. Align within 1 degree (section 6.2).
4. Measure the stylus altitude (section 3.2).
5. Do the camera plane calibration and the marker offset measurement (section 7.2).
6. On device W, hold the pen above the program origin and use Set origin (section 2.4).
7. Run the qualification session (section 8.1).
8. Record the mounting in `bench/registry.json`. The record has the date, the time, the device, the position, the measured angle, and the calibration and qualification results.

A mounting change does not change the stylus, the load, or the software. The nib changes only at the planned positions: a new nib on each device at the start of its mounting 1 and its mounting 4. These are blocks 1, 2, 7, and 8 of section 6.3. Any other change of these items is a deviation.

### 6.2 Axis and length check

At every mounting, before the runs:

1. Command a 100 mm line in +x and a 100 mm line in +y with the stylus down.
2. The device length of each line is 100 mm within 0.5 mm. The camera length is 100 mm within 0.5 mm.
3. The device x increases along the +x line, and the device y increases along the +y line.
4. The angle between the device line and the camera line is 1 degree or less.

The only permitted correction is the steps per millimetre of the motion system, before the lock. The device scales come from section 2 and are never adjusted. If a check fails after the lock, stop the block and record a deviation.

### 6.3 Blocks and order

The 12 blocks run in the order W P P W P W W P W P P W. Both devices have the same mean block position. The mounting index of a device counts its blocks in order. Mounting i of device W and mounting i of device P are then adjacent blocks.

Each block holds 60 main runs (12 programs × 5 repeats) and 9 fault runs (3 fault programs × 3 repeats) in one random order. The order of block b comes from `random.Random(20261103 + b).shuffle` on the list of 69 run labels in program order, with b from 1 to 16. `bench_programs.py --orders bench/order` writes the 16 order files once. The lock hashes them. Blocks 13 to 16 run only when section 6.6 adds mountings, in the order P W W P. Without `loops-l-v80`, a block holds 64 runs.

The totals are 720 main runs and 108 fault runs in 12 blocks. The motion time is 12.5 min per block and 2.5 h in total. With a run cycle of 1 to 1.5 min for the camera, the clip name, Begin, and Done, a block takes 70 to 105 min. A mounting change takes 45 to 90 min (an estimate). The 12 blocks then take 23 to 39 h of bench time, plus reruns.

### 6.4 Failed runs

A main run fails in three cases. `assess` rejects it. The camera check finds a motion failure (section 7.6). The operator records a `mark` with code `disturbance` or `deviceProblem`. Keep the recording. Run the same program once more at the end of the block, with run index 6. If the rerun fails too, run it once more with run index 7. No program gets more than 2 reruns in one mounting. Use a rerun only when it passes. Report every failure and every rerun.

A program is estimable for a feature on a device only when every mounting has at least 4 valid runs. For the camera-referenced features of section 9.2, a valid run also needs a valid camera record. A program that is not estimable makes its feature "not shown equivalent" (section 11.3). Report every program that is not estimable.

### 6.5 Pilot

Before the lock, with 2 mountings per device:

- 2 runs of every main program and 1 run of every fault program per mounting.
- The qualification session of section 8.1 per mounting.
- The axis and length check of section 6.2 per mounting.
- The camera validation of section 7.5 once.
- On device P, 10 s with the loaded stylus still on the glass. Record the sample count and the sample times.

The pilot checks the equipment, the software, the run sequence, the executed duration, the fault results, the camera, and the noise floor. It compares each device with its own command, never with the other device. `scriptkin bench pilot` prints these checks and writes `pilot.csv`. These checks pass before the lock:

- The motion duration of every run is within 1 percent of the command on the camera and on the device.
- On each device, the band power of every line program is between 0.5 and 2 times the command of the move list (section 5.1). Otherwise the motion system does not execute the oscillation, and the line programs leave the study before the lock.
- The noise floor of section 8.1 is measured on each device. The band power of the move list of every line program is at least 10 times the larger noise floor of the two devices. A line program that fails this rule leaves the band-power program set.
- The `fault-dwell` expectation for device P is written into section 5.3 from the still-stylus record and the pilot fault runs.

The pilot gives the measured sample interval, the noise floor, and the hold jitter. It does not estimate the mounting variance, because 2 mountings give 1 degree of freedom. The design assumes σ_mount at or below 0.25 L (section 11.8), and section 6.6 checks the assumption. The pilot feature values do not enter the main analysis, and nobody inspects the device difference of the pilot feature values. Changes after the pilot go into the protocol before the lock.

### 6.6 Interim check of the mounting variance

After block 6 and before block 7, a locked script reads the journals of blocks 1 to 6. For each device and each feature of the feature set, it estimates σ_mount from the 3 mountings of the device. It uses the one-way analysis of variance of section 11.2 per program, pooled over the program set of the feature. It prints the variance components only. It prints no device mean and no device difference. The script is `scriptkin bench interim`. It reports the components of the raw device values of every feature, and of the camera-referenced differences of the four camera features, because the main analysis compares the differences. It writes `interim.csv` and prints the row that the person who runs it adds to `bench/DEVIATIONS.md`.

If the pooled σ_mount of any feature is above 0.25 L on either device, the study adds two mountings per device. These are `M7` and `M8`, blocks 13 to 16. Rerun `bench_precision.py --sigma-mount <estimate>` with the largest estimate, and record the result. The rule is locked before the first main run. Nobody looks at a device difference before the last block (section 13).

## 7. Camera

### 7.1 Requirements

A camera is acceptable when it meets every requirement in this table. The requirements are general. A machine-vision camera with a global shutter and a 240 frames per second mode meets them without the phone steps below. Record the checks in `bench/registry.json`.

| Requirement | Value | How to check |
| --- | --- | --- |
| Frame rate | 240 captured frames per second, sustained for 30 s. No interpolated frames. | The frame clock check of section 7.3 |
| Field of view | The largest frame plus 10 mm on each side: 152 × 124 mm in the marker plane | A rule in the marker plane at the working distance |
| Pixel scale | 0.12 mm per pixel or less on both axes. The field of view needs at least 1270 × 1040 pixels at 240 frames per second. A 1920 × 1080 mode gives 0.115 mm per pixel when its short axis spans the 124 mm. | The sensor mode and the plane calibration of section 7.2 |
| Shutter | Global shutter. A rolling shutter is acceptable only with the measured row readout time and the row correction of section 7.3. | The sensor datasheet, then the readout measurement of section 7.3 |
| Exposure | 1 ms or less, locked | The camera settings and the still-marker clip |
| Focus, white balance, gain | Manual and locked, or held still by the procedure below | The camera settings and the still-marker clip |
| Lens | Fixed focal length. OpenCV removes the distortion (section 7.2). | The intrinsic calibration residual |
| Frame times | The capture time of every frame is known: from per-frame timestamps that `ffprobe` reads, or from the LED frame count of section 7.3 when the file holds playback times | `ffprobe -show_frames` and the LED clips |
| Frame integrity | No dropped and no duplicated frame in a 30 s clip | The LED frame count and the duplicate frame check of section 7.3 |
| Clip length | One run per clip, at least 30 s | A test clip |
| Mount | Fixed to the base plate above the device, optical axis normal to the surface within 2 degrees | A square against the base plate |
| Lighting | Direct current LED light or daylight, no flicker, no automatic gain | A 2 s clip of a still marker shows no intensity ripple |

The owner's camera is a Samsung Galaxy S23 Ultra phone. The facts below about the phone come from third-party pages and from a Samsung support reply, not from a Samsung specification. The checks of this section confirm them before the lock.

- Use the standard Slow motion mode. It records 1920 × 1080 at 240 frames per second (unconfirmed). Never use Super slow-mo. It records at 240 frames per second and interpolates to 960, so its frames are not captured frames (unconfirmed). The frame clock check confirms the mode: 240 frames per LED second, never 960.
- Use the main camera at 1× zoom. Set the working distance so that the short axis of the image spans the 124 mm of the field of view and no more. The pixel scale is then about 0.115 mm on both axes.
- Assume a rolling shutter. No Samsung statement on the readout time was found. Measure the row readout time with the LED method of section 7.3 and apply the row correction.
- Lock focus, exposure, and white balance when the mode allows it. When it does not, tap the marker plane to focus before each run. Then record the still-marker clip of the lighting check at the start of each block. The tracker thresholds each frame with Otsu, so an exposure drift does not move the centroid. A focus change blurs the marker edge, and the still-marker clip shows it.
- Turn video stabilisation off. Turn off every automatic edit of the clip: auto enhance, motion photo, and the slow-motion section selection. Record the whole clip at 240 frames per second.
- A phone saves a slow-motion clip for playback at a lower rate. The timestamps in the file can then be playback times, not capture times. The LED frame count of section 7.3 is the clock. The analysis uses the measured capture interval and the frame index. When the file timestamps and the LED count disagree, the LED count holds.
- Check that the saved file keeps every captured frame. The `ffprobe` frame count of a 60 s LED clip must equal 240 times the LED time within 0.1 percent. No frame is duplicated (section 7.3).
- Put the phone in flight mode. Keep it on charge. Turn notifications off. A call or a notification during a clip invalidates the run.
- Fix the phone to the base plate with a rigid clamp. The lens axis is normal to the surface within 2 degrees.
- Light the bench with direct current LED light or with daylight. Mains-powered lighting flickers at 100 Hz in the UK. A 1 ms exposure at 240 frames per second beats with that flicker.

The owner must report these facts about the camera:

- the phone model, the One UI version, and the camera app version;
- the recording mode, with the resolution and the frame rate as the app shows them;
- the camera used (main or ultra-wide) and the zoom;
- whether the mode locks focus, exposure, and white balance;
- the stabilisation setting and every automatic edit setting;
- the codec, the container, and the frame rate of the saved file as `ffprobe` reports them;
- the longest clip that the phone records at 240 frames per second;
- the clamp.

A later camera reports the same facts. The sensor mode, the shutter type, the row readout time or the sensor datasheet, and the lens focal length replace the phone items.

The marker is the 3 mm dot on the stylus (section 3.2). One video file holds one run.

### 7.2 Calibration

Scale standard. The length scale comes from a certified glass grid or from a steel rule with a calibration certificate. A printed checkerboard is not a length standard. Measure the pitch of the printed board with the rule over at least 100 mm, and use the measured pitch in the plane calibration. Record the rule, its certificate, and the measured pitch.

Intrinsic calibration, once before the lock. Take 20 or more views of the checkerboard and run OpenCV `calibrateCamera`. Record the reprojection error.

Plane calibration, at every mounting. Place the checkerboard on a spacer of height z_m on the device. Put the board x axis along the device x axis and the board origin on a marked grid point. Find the corners with `findChessboardCorners` and `cornerSubPix`. Remove the lens distortion. Fit a homography from the image to millimetres in the marker plane with the measured pitch. The calibration is valid when the residual is 0.03 mm root mean square or less.

Marker offset, at every mounting. Put the stylus tip on the marked grid point. Record a still image and take the marker position. The marker offset is the marker position minus the tip position. The stylus is tilted, so the offset is not zero. The marker sits on the stylus, so a shift of the stylus in the holder moves the marker with the nib. The nib deflection below the marker is bounded by section 3.2.

### 7.3 Frame times, rolling shutter, and tracking

Frame clock, once before the lock. Record an LED driven by a crystal-timed square wave at 1 Hz for 60 s. Count the frames per LED period. The real frame rate is the frame count divided by the LED time. It must be within 0.1 percent of the nominal rate. Record the measured rate and use it in the analysis. If the presentation timestamps of the file agree with the LED count within 0.1 percent, they are capture times. If they do not, the file holds playback times. The analysis then takes the frame time as the frame index divided by the measured rate.

LED frame count, every clip. The LED stays in the field of view, outside the device area, during every run. A period runs from one frame where the LED turns on to the next. Count the frames of every full period inside the motion interval. Every period must hold the frames of the measured rate within one frame plus 0.1 percent. All periods together must also hold their total within one frame plus 0.1 percent. Otherwise the clip is an invalid record (section 7.7).

The tolerance of one frame is needed. At a measured rate of 239.987 frames per second, a period of 1 s holds 239 or 240 frames. A simulation of clean clips at rates from 239.76 to 240.24 frames per second found that the rule fails none. A per-period tolerance of 0.1 percent alone failed 74 percent of them. The rule detects a rate error above 0.1 percent and two or more lost frames in one second. It does not detect one lost frame at a rate between two integers. With capture times, the frame interval rule below detects every lost frame. With playback times, one lost frame can pass. It then shifts the later frame times by 1/240 s, which changes `bench.motion_s` of the shortest program by 0.09 percent, or 0.024 L.

Row readout, once before the lock, for a rolling shutter only. Drive the LED at 1 kHz and record it with the camera. Record this clip with an exposure of 1/4000 s or shorter. At 1 ms, every row integrates one whole LED period and shows no band. A rolling shutter shows the LED as bands. The band pitch in rows times the row time equals the LED period. The row readout time is therefore 1 ms divided by the band pitch in rows. Record the readout time. In the analysis, correct the time of each centroid by its raw image row: t = t_frame + row × t_row.

The correction assumes that the sensor reads the rows from the top of the image. A readout in the other direction doubles the error instead of removing it. In the synthetic test of the tracker, the root mean square error on `loops-l-v80` was 0.004 mm with the correction. It was 0.137 mm without it, and 0.273 mm with the wrong direction. Confirm the direction before the lock from the movement of the bands between frames. At 240 frames per second, the bands of the 1 kHz LED move by 0.167 of a pitch per frame. In the other direction, they move by 0.833 of a pitch. A clip that the phone rotates by 180 degrees reverses the direction.

Duplicate frames, every clip. A frame whose pixel difference from the previous frame is zero everywhere is a duplicate. A clip with a duplicate or a missing frame inside the motion interval is an invalid record (section 7.7).

Frame times, every clip. Read the presentation timestamps with `ffprobe`. When the timestamps are capture times, every frame interval inside the motion interval must be within 1 percent of the median interval plus 1 µs. Otherwise the record is invalid. When they are playback times, the LED frame count is the only timing check. Never convert a clip to the Matroska format (MKV), because it rounds the timestamps to 1 ms.

Tracking, every clip:

- In the first frame, find the marker inside a 10 mm square in the marker plane. The square is centred on the commanded start point plus the marker offset. A bright region counts only when it lies completely inside the square.
- In each later frame, search the same 10 mm square, centred on the last position where the marker was found. Separate the marker with Otsu thresholding and take the intensity-weighted centroid of the largest bright region. A bright region counts only when it lies completely inside the square and its area is at least half the area of the marker dot. The two Otsu classes must differ by 32 grey levels or more.
- Remove the lens distortion from the centroid and map it through the homography. Subtract the marker offset. Apply the row correction when the shutter is rolling.
- The record is invalid when more than 2 percent of the frames have no marker.

### 7.4 Motion interval and alignment

The camera and the devices share no clock. One rule finds the motion interval on every record:

- Resample the track on the 200 Hz grid and compute the grid speed with `scriptkin.signal.kinematics` (the Savitzky-Golay derivative of section 2.3). A device record uses its accepted contact segments. A camera record uses its track.
- The motion start is the first grid sample of the first run of 6 consecutive grid samples with a speed of 2 mm/s or more. The motion end is the last grid sample of the last such run. The motion interval runs from the start to the end, gaps included.
- The motion duration is the time from the motion start to the motion end.

The bench module implements the rule once, and the lock hashes it. A run of 6 grid samples lies inside one segment. The ramps of section 5.1 cross 2 mm/s within 0.03 s of the command start, plus one grid step. The loops at 20 mm/s take the longest, because their ramp ends at the curvature speed of the start point. The motion interval is therefore the commanded motion within this tolerance.

The spatial comparison of section 8.2 aligns a camera track to its device record in time. At each lag up to 0.5 s, compute the correlation coefficient of the two grid speed profiles over their overlap inside the motion intervals. Take the lag with the largest correlation and refine it with a parabolic fit through that value and its two neighbours. The alignment is sub-frame. Report the lag per run. A plain cross-correlation sum is not used, because it pulled the lag towards zero by 3.7 ms in a test.

### 7.5 Validation before the lock

1. Frame clock test. Section 7.3 passes, and the mode of section 7.1 gives 240 captured frames per second.
2. Static test. The motion system places the marker at 25 commanded grid positions. The root mean square difference is 0.05 mm or less.
3. Speed test. Command a 100 mm line at 20 mm/s and at 40 mm/s. The camera speed agrees with the command within 1 percent. The camera length agrees with 100 mm within 0.5 mm against the scale standard.
4. Background test. The tracker finds the marker in every frame of a pilot run on each device. The backgrounds are the lit iPad screen and the Wacom surface.
5. Synthetic test. The tracker reproduces known motion from synthetic frames within the limits above.

The static and speed tests use the motion system, and section 4.2 uses the camera. The scale standard and the LED clock break the circle: the length comes from the certificate, and the time from the crystal.

### 7.6 Executed movement check

For every run with a valid camera record:

1. Align the camera track to the commanded path of its program with the path fit below.
2. Compute the distance from each camera sample to the nearest point of the commanded path.
3. The run is a motion failure when the largest distance is above 0.2 mm. It is also a motion failure when its motion duration differs from the commanded one by more than 1 percent. The rule of section 7.4 on the commanded path gives the commanded motion duration.

The path fit. The commanded path is in the stimulus frame with y down, and every record has y up. The fit therefore mirrors the commanded path first, because a rotation cannot undo a reflection. The fit aligns the record to the commanded motion in time, as in section 7.4. Each sample then pairs with the commanded position at its commanded time, which gives the first rigid fit. An iterative closest point fit to the commanded polyline repeats the rigid fit with nearest points. It stops when the root mean square changes by less than 10⁻⁶ mm, or after 100 iterations. The time pairs keep the fit from a neighbouring loop, and they fix the position along a straight line.

A motion failure is a failed run (section 6.4). Report the main analysis also without the motion failure exclusions, as a sensitivity analysis.

### 7.7 Missing or invalid camera records

The run stays in the device comparison of the features that the camera does not reference. The run leaves the accuracy analysis and the camera-referenced comparison. Report the count per device and program.

## 8. Measures

### 8.1 Sampling and noise floor

At every mounting, the motion system executes the three qualification tasks of the battery in one qualification session with `purpose` `qualification`. The tasks are `qual-reference` (the 100 mm bar), `qual-hold` (10 s still at the dot), and `qual-line` (the 100 mm line at 20 mm/s). `scriptkin.qualification.qualify` reports these values:

- `intervals` holds the count, the median, the 5th and the 95th percentile of the contact sample intervals, and the share of intervals above 30 ms.
- `hold_jitter_mm` is the root mean square distance of the still samples from their mean.
- `line_deviation_mm` is the root mean square orthogonal distance from the total least squares line.
- `scale`, `planned_channels`, and `observed_channels`.

For every main run, compute the same interval statistics from its contact strokes, plus the share of intervals above `max_gap_s` (0.05 s). Report, per device and program, the median of the run medians, the range of the 5th and 95th percentiles, and the gap shares.

The device P hold is a special case. A still Pencil can give few samples. Report the sample count of the hold with the jitter.

Noise floor. The noise floor of a device is its 4 to 12 Hz band power of the grid speed when the command holds none. Report two values per device. The first is the band power of the `qual-hold` record, at rest. The second is the median band power of the spiral runs, whose command is 0.001 (mm/s)² or less. Report both per mounting and overall. The band-power program set uses the pilot values (section 6.5).

### 8.2 Spatial accuracy

For every main run with a valid camera record, align the camera track to the device record in time (section 7.4). Then align the device samples to the camera track by a least-squares translation and rotation over the matched grid times. Report the fitted rotation. A rotation above 1 degree is a deviation. Then compute the distance between the device position and the camera position at each matched grid time. Report the root mean square and the 95th percentile per device and program. A scale error of the device is not a rigid transform, so it shows as a distance. A scale error of 6 percent gave 1.2 to 2.2 mm in the synthetic study.

For every main run, compute the trace error in the bench module. Align the raw device samples of the motion interval to the commanded path of the program by the path fit of section 7.6. Then take the root mean square of the nearest distances. The library features `spatial.trace_rmse` and `spatial.copy_rmse` depend on the stimulus origin and are descriptive only.

### 8.3 Feature agreement

Section 9 gives the features, section 10 the limits, and section 11 the statistics.

## 9. Feature set

### 9.1 The feature set

The bench module `scriptkin.bench` computes every feature of the feature set on the motion interval of section 7.4. It uses the signal functions of `scriptkin.signal` and the settings of section 2.3. The input is the accepted contact segments of `scriptkin.quality.assess`, resampled by `scriptkin.signal.resample` at 200 Hz, with the `Kinematics` of `scriptkin.signal.kinematics`. The grid samples outside the motion interval are dropped. The grid speed is the norm of the Savitzky-Golay velocity.

| Feature | Unit | Definition | Comparison scale | Program set |
| --- | --- | --- | --- | --- |
| `bench.motion_s` | s | The motion duration: the time from the motion start to the motion end. | ln | All 12 main programs |
| `bench.path_length_mm` | mm | The trapezoid integral of the grid speed over the motion interval, summed over the segments. This is the path length on the smoothed grid. | ln | All 12 main programs |
| `bench.speed_mean_mm_s` | mm/s | The mean of the grid speed over the motion interval. | ln | All 12 main programs |
| `bench.loop_height_mm` | mm | The median loop height, defined below. | ln | The 5 loops programs |
| `bench.speed_cv` | 1 | The standard deviation of the grid speed over the motion interval, with divisor n − 1, divided by its mean. With fewer than 2 grid samples, a refusal with code `too_few_values`. | absolute | The 5 loops programs |
| `bench.band_power_mm2_s2` | (mm/s)² | The Welch power of the grid speed in the 4 to 12 Hz band, 2 s windows with half overlap, at least 3 windows over the segments, trapezoid integration, as `scriptkin.signal.welch_band_power` computes it. | ln | The 3 line programs that pass section 6.5 |

`bench.loop_height_mm`, loops programs only:

1. Concatenate the grid y values of the motion interval in time order.
2. The baseline points are the first grid sample, the interior local maxima of y, and the last grid sample. Find the maxima with `scipy.signal.find_peaks(y, prominence=0.25 × (p95 − p5), distance=50)`. p95 and p5 are the 95th and 5th percentiles of y with linear interpolation. The distance of 50 samples is 0.25 s.
3. Loop j runs from baseline point j (included) to baseline point j + 1 (excluded). N is the number of loops.
4. The height of loop j equals the mean y of its two baseline points minus its minimum y.
5. The feature is the median height of the interior loops 2 to N − 1. With N below 4, the feature is a refusal with code `undefined`.

The commanded height is 20 mm at scale 1.0 and 14 mm at scale 0.7.

The plan names "duration", "path length", "mean speed", "speed variability", and "tremor-band power". The bench features above are their definitions on the motion interval. The band power is tested on the line programs only. On the other programs the feature measures the noise floor of a device, not the movement (section 8.1). The plan also names "normalised jerk". It is descriptive (section 9.3).

### 9.2 Camera-referenced features

The camera measures four of the six features on its track: `bench.motion_s`, `bench.path_length_mm`, `bench.speed_mean_mm_s`, and `bench.loop_height_mm`. The bench module computes them from the camera track with the same code, after the motion interval rule of section 7.4. The camera track is processed as a recording with the measured frame interval and `spatial_unit` `mm`. The frames with a marker form one contact stroke, which `assess` checks with `DEFAULT_QC`. A marker lost for 12 or more consecutive frames, which is 0.05 s, splits the track.

For these four features, the primary comparison uses the camera-referenced difference of each run: the device value minus the camera value on the comparison scale. The mounting mean of a program is the mean of the run differences. Section 11.2 compares the devices on these mounting means. The camera sees the executed movement on both surfaces. A device bias in the camera-referenced difference is therefore a bias of the measurement chain for the executed movement.

The raw device values of the four features are compared in the same way as a sensitivity analysis (section 11.9). For `bench.speed_cv` and `bench.band_power_mm2_s2`, the primary comparison uses the raw device values. The camera cannot measure the instantaneous speed at the limit of these features.

### 9.3 Descriptive features

Report these without limits and without a decision:

- The library features on the whole run, as a study session computes them: `timing.contact_s`, `kinematic.path_length` (the raw chord sum), `kinematic.speed_mean`, `spectral.speed_band_power`, `timing.stroke_count`, `timing.in_air_s`, `kinematic.speed_max`, `kinematic.acceleration_mean`, `kinematic.jerk_mean`, `pressure.force_mean`, `pressure.force_cv`, `tilt.altitude_mean`, `tilt.azimuth_variance`, `spatial.trace_rmse`, `spatial.copy_rmse`, `entropy.speed_sample_entropy`, `micro.spike_mean`, and the `micro.gamma_*` estimates.
- `bench.ldlj`, unit 1. Take each segment of the motion interval with a `Kinematics` result. Let D be the segment duration and S the sum of the grid chord lengths. Let J be the trapezoid integral over the grid times of the squared norm of the jerk. The segment value is ln(D⁵ J / S²). The feature is the mean over segments. The name follows the "LDLJ" of reference R3. The feature is descriptive. The third derivative amplifies the position noise of a device far above the commanded jerk, so an equivalence result would compare noise floors.
- `bench.band_power_mm2_s2` on the loops and spiral programs, and the noise floor of section 8.1.
- The raw chord sum of the device samples over the motion interval, next to `bench.path_length_mm`.

Pressure is descriptive because the bench does not calibrate force.

## 10. Equivalence limits

The limits follow two rules and a judgement. `python/studies/bench_limits.py` computes the table from the source values below (section 11.8 gives the command).

Rule B, the consequence rule, is the primary rule. L = 0.178 × the between-subject standard deviation of the feature on its comparison scale. The factor is √2 × Φ⁻¹(0.55). Let the device be fully confounded with the case status. A device bias of b standard deviations alone then gives an area under the curve of Φ(b / √2). A bias of L then gives 0.55 at most. The threshold of 0.55 is a judgement. The between-subject SD comes from an opened source on drawing or handwriting on a digitiser in healthy adults or adolescents. On the ln scale, a reported mean and SD give the SD of ln(x) as √(ln(1 + (SD / mean)²)). Where a source gives several values for one feature, the limit takes the smallest SD.

Rule A applies where no rule B source exists. L = 0.5 × the standard error of measurement (SEM) from a healthy pen-on-tablet test-retest study. No feature uses rule A, because no opened source gives such an SEM for a feature of the set.

Where neither rule has a source, the table marks the limit as a judgement and states its rule.

The rule B sources are these. Reference R6 (Gerth 2016) reports 25 adults with a mean age of 21.8 years. They wrote loops and a sentence on paper over a Wacom Intuos4 XL and on a pen screen, both at 133 Hz. Its tables give the group mean and SD of the writing duration and the writing velocity per task and surface. The article does not state that the SD is across participants. This protocol reads it as the between-subject SD of the group. Reference R4 (Bisio 2016) reports 22 adults with a mean age of 25.0 years. They wrote a sentence on a resistive tablet with a plastic stylus at 60 Hz, in two sessions one month apart. It gives the thickness of the writing, which is its vertical extent, as 14 ± 5 mm and 15 ± 4 mm. The SD is printed as a whole millimetre, so the second value lies between 3.5 and 4.5 mm.

| Feature | Scale | Limit L | Source value | Status |
| --- | --- | --- | --- | --- |
| `bench.motion_s` | ln | ± 0.036 | Loops writing duration on the pen screen, 4549.95 ± 934.84 ms (R6, Table 2). ln SD 0.203. The three other R6 durations give 0.047 to 0.054. | Source, rule B |
| `bench.path_length_mm` | ln | ± 0.044 | No source gives the path length. On the motion interval the path length is the mean speed times the motion duration, and the motion system sets the duration. The limit equals the mean-speed limit. | Judgement |
| `bench.speed_mean_mm_s` | ln | ± 0.044 | Sentence writing velocity on the pen screen, 35.88 ± 8.93 mm/s (R6, Table 4). ln SD 0.245. The three other R6 velocities give 0.045 to 0.061. | Source, rule B |
| `bench.loop_height_mm` | ln | ± 0.047 | Writing thickness, second session, 15 ± 4 mm (R4, text). ln SD 0.262. The first session gives 0.062. Thickness is a proxy for the loop height. | Source, rule B, proxy |
| `bench.speed_cv` | absolute | ± 0.030 | No source. The rule is one tenth of the smallest designed speed CV of the loops programs (0.298), the designed contrast rule of protocol 4.8. | Judgement |
| `bench.band_power_mm2_s2` | ln | ± 0.26 | No healthy source. Reference R2 reports the tablet spiral tremor amplitude of 18 people with essential tremor on consecutive days, with a standard deviation of the log10 test-retest differences of 0.16. SEM = 0.16 / √2 = 0.113 log10 = 0.26 ln on the amplitude, 0.52 ln on the power. The rule is half this SEM. | Judgement, patient group |

All limits are inclusive. They are fixed at the lock and never changed after a result. Every result is reported, including a failed limit. The ParkinsonHW data set no limit.

## 11. Statistics

### 11.1 Comparison scales

For `bench.motion_s`, `bench.path_length_mm`, `bench.speed_mean_mm_s`, `bench.loop_height_mm`, and `bench.band_power_mm2_s2`, the analysis uses the natural logarithm of the value. A bias of 0.044 on this scale is a ratio of 1.045. For `bench.speed_cv`, the analysis uses the value itself. For the camera-referenced features of section 9.2, the run value is the difference of the logarithms of the device value and the camera value.

### 11.2 Device difference with mounting effects

For each feature and program:

1. The mounting mean is the mean over the valid runs of the program in one mounting, on the comparison scale. Each device has 6 mounting means, or 8 after section 6.6.
2. The bias is the mean of the device P mounting means minus the mean of the device W mounting means.
3. The 90 percent confidence interval is the Welch interval on the two sets of mounting means. It is bias ± t × √(s²_P / m + s²_W / m). s²_P and s²_W are the variances of the mounting means, and m is the number of mountings. t is the 95th percentile of the t distribution with Welch-Satterthwaite degrees of freedom. When both variances are zero, the degrees of freedom are 2(m − 1).

No variance is pooled across programs. The interval includes the mounting effects because the mounting means carry them.

The variance components per device and program come from a one-way analysis of variance of the runs with mounting as the factor. σ²_run = MS_within. σ²_mount = max(0, (MS_between − MS_within) / n₀). With nᵢ valid runs in mounting i, N runs in total, and k mountings, n₀ = (N − Σnᵢ² / N) / (k − 1). With equal counts, n₀ is the count.

Sections 6.6, 11.4, and 11.5 pool the components over the programs of a set. The pooled σ²_run is the sum of the within squares divided by the sum of their degrees of freedom. The pooled σ²_mount is the mean over the programs of (MS_between − MS_within) / n₀, with the floor at 0 applied to the mean.

### 11.3 Equivalence test and the feature decision

For each feature and program, the two one-sided tests (TOST) p-value is the larger of two one-sided Welch t-test p-values. The first tests the bias against the lower limit −L. The second tests it against the upper limit L. The program is equivalent at level α when the p-value is at most α. At α = 0.05 this is the 90 percent interval of section 11.2 inside [−L, L]. A program that is not estimable has a p-value of 1. A standard error of 0 makes the interval a point. The p-value is then 0 when the bias lies inside the limits, and 1 when it does not.

The feature decision is the claim. The p-value of a feature is the largest p-value over the programs of its program set. This is an intersection-union test and needs no adjustment across programs. A feature is bench-equivalent when every program of its set is estimable and equivalent. A feature with a non-estimable program is "not shown equivalent".

The family is the six features of the feature set. The Holm procedure at α = 0.05 adjusts across them. Order the six feature p-values from the smallest. Feature j in this order is declared equivalent when every p-value up to and including j is at most 0.05 / (7 − its rank). Tied p-values keep the order of the table of section 9.1. Report every feature p-value, the adjusted decision, and the unadjusted decision.

The per-program decisions are descriptive. Report them with their intervals, without a claim.

### 11.4 Bland-Altman with repeated measures

Pair the mountings of the two devices by their index, 1 to m, in mounting order. The block order of section 6.3 puts mounting i of device W and mounting i of device P in adjacent blocks. A pair therefore shares its time position, nib wear, and room conditions. For each feature, program p, and pair i, the difference d_pi is the device P mounting mean minus the device W mounting mean. The average a_pi is the mean of the two mounting means. The Bland-Altman plot shows d_pi against a_pi for the points of the program set.

The limits of agreement for a mounting mean are d̄ ± 1.96 × s_d. d̄ is the mean of all d_pi of the program set. s_d² = σ²_within + σ²_between, from a one-way analysis of variance of d_pi with program as the factor. σ²_within = MS_within. σ²_between = max(0, (MS_between − MS_within) / m). The pooled limits depend on the programs in the set, so report the per-program limits of agreement too, from the m differences of each program.

The limits of agreement for a single run add the run variances. s²_single = s_d² + (1 − 1 / n̄_P) × σ²_run,P + (1 − 1 / n̄_W) × σ²_run,W, with n̄ the mean number of valid runs per mounting of each device. With 5 valid runs the factor is 4 / 5. The run variances are pooled across programs.

### 11.5 Correction with covariates and the lack-of-fit check

Fit a correction for every feature. For a feature that is not bench-equivalent, the fit is the correction. For every other feature, the fit is a report. The model maps a device W value to a device P value. The points are the mounting means of the raw device values of section 11.2, paired by mounting index as in section 11.4. There are 72 pairs for 12 programs and 6 mountings. For the four camera features, the correction does not use the camera-referenced differences. A difference holds no spread between the programs, so it leaves β unidentified. In the synthetic study of the analysis tests, the jackknife SE of β was 1.1 to 5.0 on the differences. A recording without a camera has the raw device value, and the correction maps that value.

Each program set has its own model, because the covariates identify the slope only on the 12 programs:

| Program set | Features | Model | Fitted terms |
| --- | --- | --- | --- |
| All 12 main programs | `bench.motion_s`, `bench.path_length_mm`, `bench.speed_mean_mm_s` | Covariates: P = α + β W + γ_u u + γ_v v | α, β, γ_u, γ_v |
| The 5 loops programs | `bench.loop_height_mm`, `bench.speed_cv` | Slope: P = α + β W | α, β |
| The line programs | `bench.band_power_mm2_s2` | Shift: P = α + W | α |

Here u = ln(speed / 20 mm/s) and v = ln(scale). On the 5 loops programs, u and v explain almost all of the device W differences between the programs. The residuals after the covariates then hold mostly noise, and β is not identified. In a simulation with a true β of 1.05, the covariate model gave β values near −1 and near +1 on this set. The line programs have u = v = 0 and nearly equal device W values, so only a shift is identified. A term outside the model keeps its fixed value (β = 1, γ = 0). The deliverables mark it as not fitted (section 12). The covariate model refuses a program set on which (1, u, v) has a rank below 3.

The covariate fit has three steps. The slope fit has the same steps with z = (1). The shift fit takes α as the mean of P − W over the pairs, with no λ.

1. Regress the mounting means of both devices on z = (1, u, v) by least squares. Keep the coefficients c_P and c_W and the residuals e_P and e_W.
2. Fit the Deming slope β on the residuals. The error variance ratio λ is (σ²_mount,P + σ²_run,P / n̄_P) / (σ²_mount,W + σ²_run,W / n̄_W). The variance components are the pooled components of section 11.2 of the fitted mountings. A floor of 10⁻⁶ applies to each of the two error variances. The slope is β = (S_yy − λ S_xx + √((S_yy − λ S_xx)² + 4 λ S_xy²)) / (2 S_xy).
3. The corrected value is P̂ = c_P · z + β (W − c_W · z). The intercept and the covariate coefficients of the correction are c_P − β c_W.

A program that is not estimable on both devices removes the correction of its feature, because the check below cannot then pass for every program.

Lack-of-fit check with held-out mountings. For each mounting index k from 1 to m, fit the model on the pairs of the other mountings. Apply it to the device W mounting means of mounting k. The residual of a program is its device P mounting mean of mounting k minus the corrected value. For each program, the residual bias is the mean over the m held-out mountings. Its 90 percent t interval comes from their standard deviation with m − 1 degrees of freedom. The correction passes for a program when this interval lies inside [−L, L]. The check is a lack-of-fit check with a mounting-based interval, not an out-of-sample test. Every fold subtracts the mean of the other mountings. The mean of the held-out residuals therefore adds little to the in-sample residual of the full fit. The interval carries the mounting variation. A program-dependent difference that the covariates cannot explain fails the check (section 11.8).

The published correction is the fit on all pairs. Its standard errors come from the jackknife over the m held-out fits. For a coefficient θ, SE = √((m − 1) / m × Σ(θ_(k) − θ̄)²), where θ_(k) is the fit without mounting k. No bootstrap is used, because 6 mounting pairs give only 462 distinct resamples.

### 11.6 Sampling and accuracy summaries

Report the measures of section 8 per device and program as medians with ranges over runs. These measures have no acceptance limit.

### 11.7 Quality gate measures

- The share of main runs that `assess` accepts, per device, with an exact 95 percent Clopper-Pearson interval.
- The detection fraction per fault program and device (section 5.3), with the same interval. With 18 of 18 detected, the lower bound is 0.815.

### 11.8 Precision

Three scripts run from the repository root with one environment:

```sh
UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \
    python -I python/studies/bench_programs.py
UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \
    python -I python/studies/bench_limits.py
UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \
    python -I python/studies/bench_precision.py --datasets 20000 --seed 20261103
```

`bench_programs.py` prints the table of section 5.1. `bench_limits.py` prints the table of section 10. `bench_precision.py` simulates the design. The seed 20261103 spawns one child seed per cell through `numpy.random.SeedSequence`. The script writes `studies/bench_precision.json` into the build folder of `./check` and prints the tables below. All values are in units of the limit L, so one table serves every feature. The options `--sigma-run`, `--sigma-mount`, and `--fail-share` replace a grid by one value each. The interim check of section 6.6 uses them.

In the model, a run value is the device mean plus a mounting effect plus run noise. The mounting effect has standard deviation σ_mount. It is either shared by all programs of a mounting or drawn per program. The run noise has standard deviation σ_run. The analysis is that of sections 11.2 and 11.3. The program sets are those of section 9.1: 12 programs, the 5 loops programs, and the 3 line programs. The feature decision is reported at α = 0.05 and at α = 0.05 / 6. The second value is the first Holm step, and the two values bound the power of the Holm procedure.

The assumed variance components have this rationale. A robot repeats a run far better than a person. So σ_run is a fraction of a human between-subject SD, and the limit is 0.178 of that SD. The grid covers σ_run of 0.25 and 0.5 L. A remount can shift a feature by a comparable fraction. The grid covers σ_mount of 0.1, 0.25, and 0.5 L. The design assumes σ_mount at or below 0.25 L. Section 6.6 checks the assumption at the interim and adds mountings when it fails.

Per-program decision for 6 mountings × 5 runs, share of 20000 datasets declared equivalent at α = 0.05:

| σ_run | σ_mount | Median half-width | Bias 0 | Bias 0.5 L | Bias L |
| --- | --- | --- | --- | --- | --- |
| 0.25 | 0.10 | 0.155 | 1.000 | 1.000 | 0.048 |
| 0.25 | 0.25 | 0.282 | 1.000 | 0.896 | 0.049 |
| 0.25 | 0.50 | 0.527 | 0.855 | 0.456 | 0.046 |
| 0.50 | 0.25 | 0.346 | 0.998 | 0.763 | 0.045 |
| 0.50 | 0.50 | 0.564 | 0.797 | 0.421 | 0.049 |

The share at a bias of L is 0.045 to 0.051 in every cell, and at most 0.052 at 8 × 5. The per-program test therefore keeps its 5 percent error. At a bias of L, the feature decision is 0.021 or less on the 12 programs. It is 0.033 or less on the loops and the line programs.

Feature decision for 6 × 5, share declared equivalent for every program of the set. The bias applies to every program. Each cell gives the share at α = 0.05 and at the Holm bound α = 0.05 / 6:

| σ_run | σ_mount | Bias | Mounting effect | All 12 programs | 5 loops programs | 3 line programs |
| --- | --- | --- | --- | --- | --- | --- |
| 0.25 | 0.25 | 0 | shared | 1.000 / 0.973 | 1.000 / 0.982 | 1.000 / 0.988 |
| 0.25 | 0.25 | 0 | per program | 0.999 / 0.940 | 1.000 / 0.975 | 1.000 / 0.986 |
| 0.25 | 0.25 | 0.5 L | shared | 0.727 / 0.287 | 0.790 / 0.371 | 0.828 / 0.436 |
| 0.25 | 0.25 | 0.5 L | per program | 0.271 / 0.002 | 0.578 / 0.075 | 0.719 / 0.205 |
| 0.25 | 0.50 | 0 | shared | 0.727 / 0.183 | 0.769 / 0.237 | 0.791 / 0.272 |
| 0.25 | 0.50 | 0 | per program | 0.158 / 0.000 | 0.459 / 0.008 | 0.632 / 0.058 |
| 0.50 | 0.25 | 0 | shared | 0.981 / 0.625 | 0.992 / 0.780 | 0.995 / 0.848 |
| 0.50 | 0.25 | 0 | per program | 0.978 / 0.472 | 0.991 / 0.735 | 0.994 / 0.826 |
| 0.50 | 0.25 | 0.5 L | shared | 0.317 / 0.035 | 0.466 / 0.094 | 0.562 / 0.158 |
| 0.50 | 0.25 | 0.5 L | per program | 0.041 / 0.000 | 0.266 / 0.011 | 0.450 / 0.065 |

The same at 8 × 5, which section 6.6 reaches when the interim check adds mountings:

| σ_run | σ_mount | Bias | Mounting effect | All 12 programs | 5 loops programs | 3 line programs |
| --- | --- | --- | --- | --- | --- | --- |
| 0.25 | 0.25 | 0.5 L | shared | 0.883 / 0.545 | 0.917 / 0.635 | 0.934 / 0.683 |
| 0.25 | 0.25 | 0.5 L | per program | 0.650 / 0.063 | 0.834 / 0.321 | 0.899 / 0.504 |
| 0.25 | 0.50 | 0 | shared | 0.908 / 0.516 | 0.927 / 0.577 | 0.937 / 0.618 |
| 0.25 | 0.50 | 0 | per program | 0.609 / 0.018 | 0.813 / 0.184 | 0.880 / 0.360 |

Failed runs are simulated at 6 × 5 with σ_run 0.25 L and σ_mount 0.1 L, with the rerun cap of section 6.4. At a failure share of 0.05 per run, 0.974 of datasets keep every program estimable. At 0.1, the share is 0.670. The pilot checks the procedure so that the failure share stays below 0.05.

The conclusion follows. Take σ_mount at or below 0.25 L and σ_run at or below 0.25 L. Then 6 mountings × 5 runs declare an unbiased feature equivalent on every program set in at least 0.94 of datasets. This holds at the Holm bound and with either mounting-effect structure. With σ_run at 0.5 L the share at the Holm bound falls to 0.47 for the 12-program set. Take a feature with a bias of half the limit in every program. The 12-program set declares it equivalent in 0.27 to 0.73 of datasets at α = 0.05, depending on the structure. At the Holm bound the share is 0.29 at most. The feature decision is conservative by design: an intersection over 12 programs fails when one program fails. A "not shown equivalent" result whose per-program intervals lie mostly inside the limit is a limit of power, not evidence of a difference. The per-program results show which case holds. With σ_mount at 0.5 L the unbiased 12-program share falls to 0.16 with per-program effects, and 8 mountings raise it to 0.61. This is the reason for the interim rule of section 6.6.

The σ_mount bound of 0.25 L in natural units, from `bench_limits.py`:

| Feature | σ_mount bound (0.25 L) |
| --- | --- |
| `bench.motion_s` | 0.90 percent |
| `bench.path_length_mm` | 1.11 percent |
| `bench.speed_mean_mm_s` | 1.11 percent |
| `bench.loop_height_mm` | 1.18 percent |
| `bench.speed_cv` | 0.0075 |
| `bench.band_power_mm2_s2` | 6.74 percent (0.065 on the logarithmic scale) |

The correction table of the same run uses the model of section 11.5 on the 12 programs. The true intercept is 0.5 L and the true slope is 1.05, at 6 × 5 and σ_run 0.25 L. Three truths are simulated. The `linear` truth has no program effect. The `speed` truth adds 0.5 L per doubling of the speed, which the covariates fit. The `program` truth adds offsets of ± 0.5 L that alternate between programs, which no covariate fits.

| σ_mount | Truth | Largest uncorrected bias | Held-out check passes all programs | True residual bias of the full fit (median of the largest) | Jackknife SE of β (median) |
| --- | --- | --- | --- | --- | --- |
| 0.10 | linear | 1.05 | 1.000 | 0.093 | 0.0265 |
| 0.10 | speed | 2.05 | 1.000 | 0.093 | 0.0267 |
| 0.10 | program | 1.55 | 0.263 | 0.849 | 0.0553 |
| 0.25 | linear | 1.05 | 0.999 | 0.154 | 0.0326 |
| 0.25 | speed | 2.05 | 0.999 | 0.156 | 0.0331 |
| 0.25 | program | 1.55 | 0.033 | 0.895 | 0.0823 |
| 0.50 | linear | 1.05 | 0.726 | 0.279 | 0.0795 |
| 0.50 | speed | 2.05 | 0.721 | 0.276 | 0.0799 |
| 0.50 | program | 1.55 | 0.003 | 1.016 | 0.1344 |

With a difference that the model can represent, the held-out check passes every program in 0.999 of datasets at σ_mount up to 0.25 L. The true residual bias of the published fit is then 0.09 to 0.16 L. With program offsets that the covariates cannot represent, the check passes in 0.26 of datasets or less. The true residual bias stays near 0.9 L. The check therefore detects a misfit of this size. It does not estimate the out-of-sample bias beyond the in-sample residual.

The same run simulates the slope model on the 5 loops programs and the shift model on the 3 line programs. Each cell has 5000 datasets. The line programs have u = 0, so their `speed` truth equals the `linear` truth.

| Program set and model | σ_mount | Truth | Largest uncorrected bias | Held-out check passes all programs | True residual bias of the full fit (median of the largest) | β median (quartiles) | Jackknife SE of β (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Loops, slope | 0.10 | linear | 1.05 | 1.000 | 0.079 | 1.050 (1.045 to 1.055) | 0.0070 |
| Loops, slope | 0.10 | speed | 2.05 | 1.000 | 0.172 | 1.142 (1.137 to 1.148) | 0.0074 |
| Loops, slope | 0.10 | program | 1.55 | 0.960 | 0.673 | 1.095 (1.090 to 1.100) | 0.0074 |
| Loops, slope | 0.25 | linear | 1.05 | 1.000 | 0.135 | 1.050 (1.045 to 1.055) | 0.0071 |
| Loops, slope | 0.25 | speed | 2.05 | 0.999 | 0.230 | 1.142 (1.137 to 1.148) | 0.0074 |
| Loops, slope | 0.25 | program | 1.55 | 0.384 | 0.730 | 1.095 (1.089 to 1.100) | 0.0077 |
| Loops, slope | 0.50 | linear | 1.05 | 0.806 | 0.239 | 1.050 (1.045 to 1.055) | 0.0075 |
| Loops, slope | 0.50 | speed | 2.05 | 0.662 | 0.340 | 1.142 (1.137 to 1.148) | 0.0078 |
| Loops, slope | 0.50 | program | 1.55 | 0.041 | 0.829 | 1.094 (1.089 to 1.100) | 0.0082 |
| Line, shift | 0.10 | linear | 0.45 | 1.000 | 0.046 | fixed | fixed |
| Line, shift | 0.10 | program | 0.95 | 0.958 | 0.668 | fixed | fixed |
| Line, shift | 0.25 | linear | 0.45 | 1.000 | 0.101 | fixed | fixed |
| Line, shift | 0.25 | program | 0.95 | 0.439 | 0.670 | fixed | fixed |
| Line, shift | 0.50 | linear | 0.45 | 0.850 | 0.199 | fixed | fixed |
| Line, shift | 0.50 | program | 0.95 | 0.051 | 0.732 | fixed | fixed |

On the small sets, the check passes program offsets of ± 0.5 L in about 0.96 of datasets at σ_mount 0.1 L. These passes are correct, because the true residual bias of 0.67 L lies inside the limit. The check detects the offsets only at σ_mount 0.25 L or more. On the loops programs, the slope model takes a speed-dependent difference into β. With the `speed` truth, β is 1.142 against the device slope of 1.05. The loops β therefore contains any speed dependence over the bench speeds (section 12). The W values of the simulation spread over about 13 L on the loops programs. At the designed spread of `bench.speed_cv` (0.298 to 0.342), the jackknife SE of β is 0.054 at σ_mount 0.1 L and 0.064 at 0.25 L. At the spread of `bench.loop_height_mm` (20 and 14 mm), it is 0.0075.

`bench_precision.py` takes every statistic from `scriptkin.bench.stats`, so the precision study simulates the statistics of the analysis.

### 11.9 Sensitivity analyses

Report the analysis of sections 11.2 to 11.5 also:

- without the motion failure exclusions of section 7.6,
- on the raw device values of the four camera-referenced features,
- per task, per scale, and per speed, pooled over the other factors,
- with the mounting means replaced by the medians of the runs.

## 12. Deliverables for the consequence experiment

The bench delivers `bench/results/deliverables.json` with, for each feature and program:

- The bias on the comparison scale, its standard error, and its 90 percent interval.
- For a logarithmic feature, the bias factor exp(bias) with its interval. The factor applies to the feature in any unit. For `bench.speed_cv`, the bias itself.
- The variance components σ²_run and σ²_mount for each device.
- The correction model of the program set and the coefficients α, β, γ_u, and γ_v with their jackknife standard errors, fitted on the raw device values. A term outside the model of section 11.5 is marked as not fitted. The β of the loops programs contains any speed dependence of the device difference over the bench speeds.
- The held-out residual bias with its interval.

The deliverables give each difference per program, because the bench shows whether the difference depends on the task, the scale, and the speed. For each feature, the deliverables state the range in which the factors hold. The scales are 0.7 and 1.0. The speeds are 20 to 80 mm/s for the loops, or 20 to 40 mm/s without `loops-l-v80`. They are 20 to 40 mm/s for the spiral, and 20 mm/s with a 5 to 11 Hz oscillation for the line. The consequence experiment and the ParkinsonHW example of the plan apply the factor of the program closest to their data in task, scale, and speed. They state the choice.

No standardised difference is delivered. A difference divided by the robot's run and mounting variation does not transfer to a human population, whose between-subject SD is far larger.

## 13. Stop rules, deviations, and the deviation log

Stop before the lock, and do not lock, when any of these holds:

- The camera validation (section 7.5) or the motion qualification (section 4.2) fails.
- The axis and length check (section 6.2) fails on a device after the steps per millimetre correction.
- The parity test (section 2.4) fails.
- The Mac device checks of section 2.2 fail.
- The nib deflection of section 3.2 is 0.02 mm or more.
- The pilot median sample interval of a device is above 25 ms (40 Hz, `min_rate_hz`).
- The pilot checks of section 6.5 fail.

Stop a block after the lock, remount, and record a deviation when any of these holds:

- The qualification session of the mounting shows a hold jitter above twice the pilot median, or a line deviation above 0.1 mm.
- The axis and length check fails.
- Three consecutive runs fail.
- The stylus altitude or the load is outside its tolerance.

No person and no script compares the feature values of the two devices before the last block. The interim check of section 6.6 prints variance components only. Every access to the feature values during the main runs is a row in `bench/DEVIATIONS.md`. The row gives the person, the date, the script, and what it printed.

A deviation is any departure from this protocol after the lock. Record every deviation as a table row in `bench/DEVIATIONS.md`. The row has the date and time, the runs affected, the departure, the cause, the decision, and the person. Commit each row when it is written, so that the Git history dates the row. Analysis changes after the lock are deviations and are reported in the paper as post hoc.

## 14. Lock procedure

The lock registers everything that the main runs depend on. Do it after the pilot and before the first main run.

1. Complete the parity test, the Mac device checks, the camera validation, the motion qualification, the friction and stiffness measurements, and the pilot.
2. Write the pilot results into the protocol: the `fault-dwell` expectation of device P, the band-power program set, and the `loops-l-v80` decision. Run `./check` at the lock commit. It must pass.
3. Put the pilot measurements and the chosen constants into `bench/registry.json`. Run the three scripts of section 11.8. Copy the precision report to `bench/results/bench_precision.json`.
4. Generate the run orders (section 6.3), the program files (section 5.1), the controller files (section 4.1), and `contract/bench_programs.json` (section 2.4).
5. Write `bench/BENCH_LOCK.json` with `scriptkin bench lock`. It holds the protocol version and the repository commit hash. It holds `app.build` and `app.sourceRevision` of SCRIPT Capture and of SCRIPT Bench. It holds the SHA-256 of each file in this list:
   - `docs/BENCH_PROTOCOL.md`
   - `bench/registry.json`
   - every file under `bench/programs/` and `bench/order/`
   - `contract/bench_programs.json`
   - `python/uv.lock`
   - `python/studies/bench_programs.py`, `python/studies/bench_limits.py`, and `python/studies/bench_precision.py`
   - the translation script of section 4.1
   - the precision report `bench/results/bench_precision.json`

   The commit hash covers the analysis code, the bench module, and the interim script of section 6.6.
6. Commit the lock file. The commit hash is the lock reference.
7. Write the commit hash and the SHA-256 of `bench/BENCH_LOCK.json` as the first row of `bench/DEVIATIONS.md`.
8. An independent reviewer checks the lock file against the files, the builds, and the commit before the first main run. The reviewer records the check as the second row of `bench/DEVIATIONS.md`.

The lock file does not contain its own digest. The paper and the supplement cite the lock commit.

## 15. Data and file naming

All bench files live under `bench/` in the repository. The recordings and the video are large and live in the study store of the records drive. The study store path is in `bench/registry.json`.

| Path | Content |
| --- | --- |
| `bench/registry.json` | Devices, software versions, fixture measurements, friction and stiffness, camera facts and checks, motion settings, one record per mounting, pilot measurements, constants, and the move list digests. |
| `bench/programs/<program>.json` | The move list of each program, from `bench_programs.py --write`: the program, the start point, and the moves. |
| `bench/programs/<program>.<controller>` | The controller file generated from the move list by the translation script. Its SHA-256 is the trajectory digest. |
| `bench/order/block-<bb>.csv` | The run order of block bb (01 to 16), with position, program, and repeat, from `bench_programs.py --orders`. |
| `bench/runs.csv` | One row per run, with the columns `run_id`, `device`, `mounting`, `block`, `position`, `program`, `run_index`, `session_id`, `trial_index`, `clip`, `status`, and `deviation_row`. The status is `planned`, `recorded`, or `void`. A void row names its deviation row. The clip equals the run id. |
| `bench/DEVIATIONS.md` | The deviation log and the data access log. |
| `bench/BENCH_LOCK.json` | The lock file. |
| `bench/results/` | The analysis outputs: `run_status.csv`, `features.csv` in the `FeatureTable` format with its provenance file, `bench_features.csv`, `agreement.csv`, `bland_altman.csv`, `correction.csv`, `sampling.csv`, `accuracy.csv`, `gate.csv`, `interim.csv`, `pilot.csv`, `bench_precision.json`, and `deliverables.json`. |

Run ids are `<device>-<mounting>-<program>-r<run index>`, for example `P-M3-loops-s-v40-r2`. A rerun has the run index 6 or 7.

Each block is one journal session. The capture tool exports it as `SCRIPT_BENCH_<device><mounting>_<session8>/`. Each run is one trial, in block order. `trialStarted.bench` names the program, the run index, the trajectory digest, and the clip (section 2.4). The clip is the run id.

The camera files are `<run id>.mov`, `<run id>.frames.json` from `ffprobe`, and `<run id>.track.csv` with `<run id>.track.json` from the tracker. The frames file comes from `ffprobe -v error -select_streams v:0 -show_frames -of json <run id>.mov`. The track rows hold one row per frame, and the track facts name the clip, the frames file, and the calibration by their SHA-256. They live under `camera/<device>-<mounting>/` in the study store. The camera steps write one JSON file each: the lens, the plane, the calibration of each mounting, the frame clock, and the row readout.

The analysis reads `bench/runs.csv`, `bench/registry.json`, the lock file, the move lists, the journals, and the tracks, and writes `bench/results/`. One command does this: `scriptkin bench analyse <repository root>`. The lock commit registers it. The command checks the digests of the lock file and the build of every journal first. It refuses to run while a block of the registry has no row or a row is still `planned` (section 13).

The analysis reads these keys of `bench/registry.json`. Other keys are allowed.

| Key | Content |
| --- | --- |
| `studyStore` | The path of the study store. |
| `mountings` | One record per mounting: `device`, `mounting`, `block`, `originMM` (the stimulus origin in plane millimetres, `x` and `y`), and `qualificationSession` (optional, for the noise floor of the hold). |
| `constants` | The results of the pilot: `bandPowerPrograms`, `excludedPrograms`, and `faultDwellGapsOnP`. |

In `bench/results/`, `run_status.csv` gives each run its outcome of section 6.4, its camera state, and the time lag of its camera track. `features.csv` holds the library features of every recorded run, with the run id in the `participant` column. `bench_features.csv` holds the bench features of each run from the device and from the camera. The statistics files name the analysis in their first column: `main`, or a sensitivity analysis of section 11.9 (`no_motion_exclusion`, `raw_device`, `median`, `by_task`, `by_scale`, `by_speed`). A value that cannot be computed is empty, with its reason in the `reason` column.

The other commands are `scriptkin bench pilot`, `scriptkin bench interim`, `scriptkin bench lock`, and `scriptkin bench start-point`. The last one prints the commanded start point of a run in plane millimetres for `scriptkin bench track`. The camera steps are `scriptkin bench lens`, `plane`, `marker-offset`, `clock`, `readout`, and `track`. Every command exits with 0 when it is done, 1 when it refuses, and 2 for a damaged input.

## 16. What the paper may and may not claim

The paper may claim:

- For each feature of the feature set, whether the two measurement chains are equivalent within the registered limit on every program of its set. The error rate is Holm-adjusted across the six features. The claim holds for the tested units, software versions, surfaces, mountings, stylus angle and azimuth, load, region of the active area, and speeds.
- For the four camera-referenced features, that the comparison holds for the executed movement of each run. For `bench.speed_cv` and `bench.band_power_mm2_s2`, that it holds for the commanded movement, executed within the camera check.
- The size of the bias and its interval per program, as descriptive results, and the limits of agreement.
- The residual bias after the correction on held-out mountings, described as a lack-of-fit check with a mounting-based interval.
- The sample interval, the gap share, and the noise floor of each device. The spatial accuracy of each device against the camera and the command, for the path, the mean speed, the duration, and the loop height.
- Which fault programs the quality checks detect on each device.
- The consequence of the measured bias and noise in the synthetic cohorts, as the plan defines, for the measurement chain.

The paper may not claim:

- Anything about people, their handwriting, their health, or any mental health condition.
- That the devices agree for human movement. The motion system is not a person. Glass and the tablet surface change human movement, and the bench cannot measure that. Reference R6 found that the same adults wrote loops 20 percent faster on a smooth pen screen than on paper. This surface effect is about four times the speed limit.
- That the correction corrects human recordings between devices. For a person, the device difference is the chain difference plus the surface effect on behaviour, and the bench measures the chain only.
- That the consequence results cover more than the measurement chain. They are a lower bound of the device effect for people.
- That the devices agree in smoothness or jerk. `bench.ldlj` is descriptive.
- That the devices agree in tremor-band power beyond the designed oscillations of the line programs at 20 mm/s.
- The camera as a reference for band power, jerk, or instantaneous timing.
- Equivalence from a per-program result alone, from a failed limit, or from a limit changed after a result.
- A transfer of a robot-standardised difference to human features.
- Agreement outside the tested speeds, sizes, angle, azimuth, load, region, or units. One unit of each device was tested.
- Agreement of pressure, tilt, or hover. These are descriptive here.
- A report rate for the Wacom tablet other than the measured one.

The discussion of the paper cites reference R6 for the surface effect as the reason for the two claims about people and the correction.

## References

Each reference was opened on 3 November 2026 at the address given.

- R2. Elble RJ, Ellenbogen A. Digitizing tablet and Fahn-Tolosa-Marín ratings of Archimedes spirals have comparable minimum detectable change in essential tremor. Tremor and Other Hyperkinetic Movements. 2017;7:481. DOI 10.5334/tohm.344. Opened at https://tremorjournal.org/articles/344. Values used. The device was a Wacom Intuos 3 with a stated accuracy of ± 0.25 mm at 100 samples per second. The tablet tremor amplitude, the mean of four spirals, had an ICC of 0.97 (0.91 to 0.99). The standard deviation of the log10 test-retest differences was 0.16. The minimal detectable change was 51 percent of the baseline geometric mean. The study had 18 people with essential tremor on two consecutive days.
- R3. Toffoli S, Abbate C, Lunardini F, Corno E, Diani N, Gallucci A, Tomasini E, Trimarchi PD, Ferrante S. Handwriting in mild cognitive impairment: reliability assessment and machine learning-based screening. JMIR Aging. 2025;8:e73074. DOI 10.2196/73074. Opened at https://pmc.ncbi.nlm.nih.gov/articles/PMC12504891/. Values used. The name LDLJ, and its test-retest reliability of 0.83 to 0.93 for cursive tasks with a sensorised ink pen at 50 Hz. No SEM is reported.
- R4. Bisio A, Pedullà L, Bonzano L, Ruggeri P, Brichetto G, Bove M. Evaluation of handwriting movement kinematics: from an ecological to a magnetic resonance environment. Frontiers in Human Neuroscience. 2016;10:488. DOI 10.3389/fnhum.2016.00488. Opened at https://pmc.ncbi.nlm.nih.gov/articles/PMC5040726/. Values used. 22 adults with a mean age of 25.0 years wrote a sentence five times in two sessions one month apart. The device was a resistive tablet with a plastic stylus at 60 Hz. Thickness was 14 ± 5 mm and 15 ± 4 mm with ICC 0.78 and regression slope 0.75. Duration was 6.01 ± 1 s and 5.71 ± 1 s with ICC 0.69 and slope 0.74. No SEM is reported.
- R5. Sisti JA, Christophe B, Seville AR, Garton ALA, Gupta VP, Bandin AJ, Yu Q, Pullman SL. Computerized spiral analysis using the iPad. Journal of Neuroscience Methods. 2017;275:50-54. DOI 10.1016/j.jneumeth.2016.11.004. Opened at https://pmc.ncbi.nlm.nih.gov/articles/PMC5308231/. Prior work. An iPad with a finger or a capacitive stylus at 60 Hz or less against a Wacom Intuos 4 with a ballpoint stylus. 15 healthy people and 16 patients took part. No Apple Pencil was used. All nine indices correlated between the devices, with R² from 0.52 to 0.90.
- R6. Gerth S, Klassert A, Dolk T, Fliesser M, Fischer MH, Nottbusch G, Festman J. Is handwriting performance affected by the writing surface? Comparing preschoolers', second graders', and adults' writing performance on a tablet vs. paper. Frontiers in Psychology. 2016;7:1308. DOI 10.3389/fpsyg.2016.01308. Opened at https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2016.01308/full. Values used. 25 adults, mean age 21.8 years, wrote on paper over a Wacom Intuos4 XL and on a pen screen, both at 133 Hz. Loops: duration 4760.90 ± 1273.86 ms on paper and 4549.95 ± 934.84 ms on the screen, velocity 104.55 ± 36.75 and 125.06 ± 31.97 mm/s. Sentence: duration 5039.54 ± 1414.62 and 5872.78 ± 1823.93 ms, velocity 30.46 ± 7.90 and 35.88 ± 8.93 mm/s.
- W1. Wacom. Wacom Intuos Pro (2025) product page. Opened at https://www.wacom.com/en-us/products/pen-tablets/wacom-intuos-pro. Values used in section 2.2.
- M1. Evil Mad Scientist. AxiDraw V3/B6 product page. Opened at https://shop.evilmadscientist.com/945. Values used in version 1.1 of section 4.3.
- M3. Bantam Tools. NextDraw 8511 product page. Opened on 4 November 2026 at https://bantamtools.com/products/bantam-tools-nextdraw-8511. Values used in section 4.3.
- M4. Evil Mad Scientist. EBB (EiBotBoard) command set, firmware 3.0 and later. Opened on 4 November 2026 at https://evil-mad.github.io/EggBot/ebb.html. Values used in section 4.3.
- M2. Creality. Ender-3 V3, V3 Plus, and V3 KE technical specifications. Opened at https://www.creality3dofficial.com/blogs/null/exploring-the-ender-3-v3-plus-3d-printer. Values used in section 4.3.
- Protocol 4.8 and its reference implementation, `Screening_Device/BENCH_PROTOCOL.md` and `Screening_Device/bench_reference/` in the earlier project folder. Reused: the fixture and holder design, the camera method, the axis and length check, the lock procedure, and the designed contrast rule.

Reference R1 of version 1.0 (Goizueta 2025) is withdrawn. Its SEM table is internally inconsistent, and its task, population, and device differ from the bench.
