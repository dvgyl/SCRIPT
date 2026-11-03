# Bench protocol

Version 1.0. The status is draft until the lock (section 14). After the lock, every change is a deviation (section 13).

This protocol defines the robotic bench study of `docs/PAPER_PLAN.md`. A motion system moves a stylus along registered programs on two devices. A camera records the executed movement. The study has no participants and uses no data from people. It makes no mental health claim and no claim about people.

A reader with the hardware can run the study from this document. Sections 2 to 7 cover the devices, the fixture, the motion system, the programs, the mountings and the run order, and the camera. Sections 8 to 12 cover the measures, the features, the equivalence limits, the statistics, and the deliverables for the consequence experiment. Sections 13 to 16 cover the stop rules and deviations, the lock, the file layout, and the claims that the paper may make.

Two scripts belong to this protocol. `python/studies/bench_programs.py` computes the geometry, the timing, and the motion demands of every program and writes the move lists. `python/studies/bench_precision.py` simulates the precision of the design. Section 11.8 gives their commands and seeds.

## 0. Terms

| Term | Meaning |
| --- | --- |
| Device P | The iPad Pro with the Apple Pencil Pro and SCRIPT Capture in bench mode. |
| Device W | The Wacom Intuos Pro Medium (2025) with the Pro Pen 3, a Mac, and SCRIPT Bench. |
| Program | A registered list of timed moves that the motion system executes (section 5). |
| Run | One execution of one program on one device. One run is one trial in one journal. |
| Mounting | One placement of a device in the fixture. Section 6.1 defines a mounting change. |
| Block | All runs of one mounting. |
| Journal | A SCRIPT recording in the format of `docs/DESIGN.md` section 3. |
| Contact segment | A part of a contact stroke that `scriptkin.quality.assess` accepts. |
| Comparison scale | The scale on which a feature is compared, either the natural logarithm of the value or the value itself (section 11.1). |
| Mounting mean | The mean of the valid runs of one program in one mounting, on the comparison scale. |
| Bias | The mean of the device P mounting means minus the mean of the device W mounting means, for one feature and one program. |
| Equivalence limit L | The registered bound on the bias of one feature (section 10). |
| Camera track | The marker positions of one run from the camera, in millimetres in the marker plane. |
| Executed movement | The camera track of one run, aligned to its commanded path. |
| Lock | The registration of this protocol, the programs, the analysis code, and the constants before the main runs. |
| Deviation | Any departure from this protocol after the lock. |

## 1. Purpose and claim

The bench tests whether the SCRIPT measurement chain gives the same feature values on device P and on device W for the same executed movement.

The claim is the claim of `docs/PAPER_PLAN.md` section 2.2. For each feature in section 9, the two chains give equivalent values within the limit of section 10 for every program, or they do not. Where they do not, a correction learned on the bench reduces the difference on unseen mountings to a stated size. The claim covers only the tested units, software versions, surfaces, mountings, stylus angle, load, and speeds.

The bench answers these questions:

1. Agreement. For each feature and program, is the bias within the limit, with mounting effects in its confidence interval?
2. Correction. After the Deming correction, is the residual bias on held-out mountings within the same limit?
3. Sampling. What are the sample interval, its spread, and the gap share of each device during the programs?
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
- A new standard Pencil tip at the start of the study. Record `surface.nib`. Inspect the tip at the start of each block. Replace a tip that shows wear, and record the replacement.
- Low Power Mode off. Battery above 50 percent at the start of each block. No other app in the foreground. Notifications off.

### 2.2 Device W

The tablet is a Wacom Intuos Pro Medium (2025), model PTK670, with the Wacom Pro Pen 3. These values come from the Wacom product page (reference W1, opened on 3 November 2026):

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
- Use the standard nib. Fit a new nib at the start of the study. Record every replacement.
- Record the Wacom driver version, the macOS version, the Mac model, and the SCRIPT Bench version.
- Turn off every driver feature that changes the pen reports. Record the driver settings.

### 2.3 Analysis software

- `scriptkin` at the locked commit (section 14), with the Python version and `python/uv.lock`.
- OpenCV and `ffprobe` for the camera (section 7). Record their versions.
- The quality policy is `scriptkin.quality.DEFAULT_QC`: `max_gap_s` 0.05, `min_segment_s` 0.1, `min_rate_hz` 40, `reject_unresolved` true.
- The preprocessing is `scriptkin.signal.DEFAULT_PREPROCESSING`: `grid_hz` 200, Savitzky-Golay `window` 11 and `polynomial` 3, `welch_window_s` 2, `band_hz` 4 to 12.

### 2.4 Capture software requirements

The build phase of the plan delivers two capture tools. Both write journal format 2.0 (`docs/DESIGN.md` section 3). The design is authoritative for every line shape. This section lists only what the bench needs from it.

Both tools:

- `sessionOpened.purpose` is `bench`. The header has no participant keys. Its `bench` object holds the device role (`P` or `W`), the mounting code, the motion system id and version, and the protocol version and SHA-256.
- One journal holds one mounting of one device, which is one block (section 6.3).
- Each run is one trial. `trialStarted.spec` holds the task specification at the program scale (section 5.1), so the journal is self-contained.
- `trialStarted.bench` holds the program id, the run index, and the SHA-256 of the executed move list. The tool copies the digest from the bundled programs file, so the operator never types it. The program id names the task, the scale, the speed, and the fault.
- The operator taps Begin before the stylus lowers and Done after the stylus lifts. Done ends the trial with reason `operatorDone`.
- The session refuses every questionnaire, safety, and handoff line.

SCRIPT Capture bench mode on device P:

- Every sample, correction, hover, and finger line is written as in a study session.

SCRIPT Bench on the Mac for device W:

- It reads the tablet reports through `NSEvent` with mouse event coalescing off.
- `sessionOpened.input` is `tablet`. The `tablet` object records the tablet model, its asset code, the driver version, and the pen.
- Each report gives a `sample` line. `x` and `y` are tablet lines from `absoluteX` and `absoluteY`, with the origin at the top left and y down. `force` is the normalised pressure. `tiltX` and `tiltY` are the native tilt values. `est` is 0.
- `scale` is `{"kind": "tablet", "linesPerMM": 200, "mmPerPoint": 0.005}`, from the pen resolution in section 2.2.
- `channels` plans `force`, `tiltX`, `tiltY`, and `hover`. Record whether the driver reports rotation.
- A pen report in proximity without contact gives a `hover` line without height.
- The window must keep the keyboard focus. Loss of focus during a trial ends the trial with reason `backgrounded`.

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
- The holder must not touch the device surface. A 3 mm matte white dot on a black field is fixed to the holder for the camera (section 7.1).
- Measure the altitude with a digital angle gauge at the start of each block. If the altitude differs from 60 degrees by more than 1 degree, adjust the holder before the block.

The angle and the load are fixed because the devices convert contact force to pressure in different ways. The bench does not calibrate force. Pressure features are descriptive only (section 9.3).

## 4. Motion system

### 4.1 Requirements

Any motion system for this bench must meet every requirement in this table. `bench_programs.py` computes the demands of the programs (section 5.1).

| Requirement | Value | Reason |
| --- | --- | --- |
| Travel | At least 135 × 125 mm | The largest frame is 112 × 104 mm, plus 10 mm on each side for the approach. |
| Position resolution | 0.025 mm per step or finer | Four steps per 0.1 mm of path, so that step quantisation stays below the device noise. |
| Repeatability | 0.1 mm or better at the program speeds | The executed movement check allows 0.2 mm (section 7.6). |
| Speed range | 5 to 60 mm/s | The programs command 6.8 to 53.9 mm/s. |
| Speed accuracy | Within 1 percent of the command over a 100 mm line | The camera measures it (section 4.2). |
| Acceleration | At least 2000 mm/s² | Twice the peak commanded acceleration of 852 mm/s². |
| Timed moves | The controller accepts a move to an end point in a given duration, or a G1 move with a feed rate | The programs are lists of timed moves. |
| Lookahead | Off, or with a known model | Corner smoothing changes the executed path. The camera check decides whether the executed path is within 0.2 mm. |
| Input shaping | Off | It changes the executed timing. |
| Stylus load and angle | Set by the holder of section 3.2, not by the machine | The load must be constant and known. |
| Stylus lift | A vertical lift of at least 10 mm with a controlled lowering speed of 10 mm/s or less | A fast drop bounces the nib. |
| Device motion | The device stays still. If the device moves, the camera must also track a marker fixed to the device. | The executed movement is the stylus movement relative to the device. |
| Vibration at rest | Marker position noise of 0.02 mm or less (camera, 2 s still) | Vibration adds to the measured noise. |
| Command record | The controller file of each program is generated by a script from the registered move list and hashed | The commanded trajectory is known from the file, and the camera verifies the execution. |
| Settings record | Every motion setting is in `bench/registry.json` | A reader can repeat the execution. |

The translation script from a move list to a controller file is part of the lock. It must not change the end points or the durations.

### 4.2 Qualification test

Do this test for the chosen motion system before the pilot. The camera must pass section 7.5 first. Record every result in `bench/registry.json`.

1. Mount a blank plate at the device position and the marker on the holder.
2. Command the marker to 25 grid positions, 5 × 5 at 25 mm spacing. Hold 2 s at each. The root mean square difference between the camera position and the command is 0.05 mm or less.
3. Repeat step 2 five times. The root mean square spread of the five positions at each point is 0.1 mm or less.
4. Command a 100 mm line in +x at 20 mm/s and at 40 mm/s, then the same in +y. The camera speed agrees with the command within 1 percent. The camera length agrees with 100 mm within 0.5 mm.
5. Execute `loops-s05-v40` five times. Align each camera track to the commanded path (section 7.6). The largest distance is 0.2 mm or less in each run. The motion duration is within 1 percent of 4.71 s.
6. Execute `spiral-s05-v40` five times with the same limits and a duration within 1 percent of 4.59 s.
7. Record 2 s of camera video with the marker at rest. The position noise is 0.02 mm or less on each axis.
8. Lower the stylus onto the plate ten times. The camera shows no bounce. The marker position is stable within one frame after contact.

If any step fails, change the settings and repeat the test. If the system cannot pass, choose another candidate.

### 4.3 Candidates

NextDraw pen plotter, the successor of the AxiDraw. The AxiDraw product page (reference M1) gives these values. The resolution is 80 steps per mm. The reproducibility is better than 0.1 mm at low speeds. The maximum speed is 38 cm per second. The vertical pen travel is 17 mm. A Python API and a direct serial command protocol give timed moves. The page names the Bantam Tools NextDraw 8511 as the replacement. The device stays still under the moving arm. The arm is a belt-driven gantry, so its rigidity at the stylus must pass section 4.2. The pen lift is a servo, so the lowering speed must be set low. The NextDraw page was not reachable on 3 November 2026 (HTTP 429). Confirm its figures before purchase.

GRBL pen plotter. A budget belt plotter with GRBL firmware accepts G1 moves with feed rates. GRBL plans junctions with a junction deviation setting, so the executed path differs from the command at corners. The frame rigidity and the repeatability vary between kits. The qualification test decides.

Klipper 3D printer, Ender-3 V3 KE. The Creality page (reference M2) gives these values. The build volume is 220 × 220 × 240 mm. The speed is 500 mm/s or less. The acceleration is 8000 mm/s² or less. The structure is Cartesian, and the firmware is based on Klipper. The bed moves in y, and the device with it. The camera must then track a device marker as well as the holder marker. The device cable and the tablet weight load the moving bed. Klipper's lookahead needs the registered settings of protocol 4.8: `SQUARE_CORNER_VELOCITY`, `MINIMUM_CRUISE_RATIO` 0, and input shaping off. The executed path model of the reference implementation (`bench_reference/src/bench_reference/motion.py`) still applies.

The recommended system is the NextDraw 8511. The device stays still, which keeps the camera method of section 7 simple. The resolution and the repeatability meet section 4.1. The Python API gives timed moves without a lookahead planner. The open risks are the gantry rigidity and the servo lift. Section 4.2 tests both. The Ender-3 V3 KE is the fallback when the NextDraw fails section 4.2. It has the highest acceleration and a modelled planner, at the cost of a moving device.

## 5. Programs

### 5.1 Main programs

The main programs use the two battery tasks `loops` and `spiral-trace` of `contract/battery.json` (`scriptkin.battery`), at two scales and two speeds. Coordinates are in the stimulus frame. The origin is at the top left, x goes to the right, y goes down, and the unit is the millimetre.

The loops curve, with the parameter t from 0 to 16π:

- x(t) = 8 + 12 t / (2π) + 4 sin t
- y(t) = 24 − 10 (1 − cos t)

The spiral curve, with θ from 0 to 4π and r(θ) = 8 + 21 θ / (2π):

- x(θ) = 52 + r cos θ
- y(θ) = 52 − r sin θ

A program at scale s multiplies both coordinates by s. The bench mode shows the stimulus at the same scale, so the drawn figure and the stimulus agree.

The tangential speed profile:

- For the loops, the speed is proportional to the cube root of the curvature radius. The radius is capped at 40 mm. The constant makes the mean speed over the run equal to the program speed.
- For the spiral, the tangential speed is constant and equal to the program speed.

The move list samples the time parametrisation every 10 ms. Each move is an end point and a duration. The last move can be shorter. `bench_programs.py --write bench/programs` writes the lists.

| Program | Task | Scale | Mean speed (mm/s) | Path (mm) | Duration (s) | Speed range (mm/s) | Speed CV | Peak acceleration (mm/s²) | Smallest radius (mm) | LDLJ | Band power (mm/s)² | Moves |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `loops-s1-v20` | loops | 1.0 | 20 | 376.7 | 18.84 | 6.8 to 26.9 | 0.281 | 107 | 0.44 | 16.1 | 0.011 | 1884 |
| `loops-s1-v40` | loops | 1.0 | 40 | 376.7 | 9.42 | 13.7 to 53.9 | 0.282 | 427 | 0.44 | 16.1 | 1.73 | 942 |
| `loops-s05-v20` | loops | 0.5 | 20 | 188.4 | 9.42 | 6.8 to 26.9 | 0.282 | 213 | 0.22 | 16.1 | 0.43 | 942 |
| `loops-s05-v40` | loops | 0.5 | 40 | 188.4 | 4.71 | 13.7 to 53.9 | 0.282 | 852 | 0.22 | 16.1 | 22.5 | 471 |
| `spiral-s1-v20` | spiral-trace | 1.0 | 20 | 367.5 | 18.37 | 20.0 | 0.000 | 52 | 7.55 | 11.9 | 0 | 1838 |
| `spiral-s1-v40` | spiral-trace | 1.0 | 40 | 367.5 | 9.19 | 40.0 | 0.000 | 208 | 7.55 | 11.9 | 0 | 919 |
| `spiral-s05-v20` | spiral-trace | 0.5 | 20 | 183.7 | 9.19 | 20.0 | 0.000 | 104 | 3.77 | 11.9 | 0 | 919 |
| `spiral-s05-v40` | spiral-trace | 0.5 | 40 | 183.7 | 4.59 | 40.0 | 0.000 | 407 | 3.77 | 11.9 | 0 | 460 |

The speed CV is time-weighted on the 200 Hz grid. LDLJ is the commanded log dimensionless jerk of section 9.2. Band power is the commanded 4 to 12 Hz band power of the speed, computed as `spectral.speed_band_power` computes it.

The speeds and the scales keep every program above 4 s, because `spectral.speed_band_power` needs three Welch windows of 2 s. The mean speeds of 20 and 40 mm/s are slow writing speeds. The claim does not extend to faster movement.

### 5.2 Run sequence

For every run:

1. Move the stylus above the start point of the program at the lift height.
2. The operator taps Begin.
3. Lower the stylus. Wait 0.5 s.
4. Execute the move list.
5. Wait 0.3 s. Lift the stylus.
6. The operator taps Done within 5 s.

The camera records from before step 3 to after step 5 (section 7.4).

### 5.3 Fault programs

Each fault program is the program `loops-s1-v20` with one change. The faults test the quality findings of `scriptkin.quality.assess` and the features that a fault must change.

| Program | Change | Expected result on device P | Expected result on device W |
| --- | --- | --- | --- |
| `fault-dwell` | The stylus stops for 0.5 s on the surface after loop 4, then continues. | A `gaps` finding, because a still Pencil gives few or no samples. The contact splits into two segments. `timing.contact_s` is at least 0.4 s below the median of the clean runs of the block. | Record the result. The tablet reports a still pen, so no `gaps` finding is expected. `timing.contact_s` is within 2 percent of the clean median plus 0.5 s. |
| `fault-lift` | The stylus lifts for 0.3 s after loop 4, then lowers and continues. | `timing.stroke_count` is 2. | `timing.stroke_count` is 2. |
| `fault-truncated` | The program stops after loop 4. | `kinematic.path_length` is at most 0.6 times the clean median. `spatial.copy_rmse` is above the 95th percentile of the clean runs. | The same. |

A fault run counts as detected when every expected result for its device holds. Report the detection fraction per fault and device with an exact 95 percent Clopper-Pearson interval.

One operator fault is not a program. Before the lock, on each device, send the capture tool to the background during a run once. The journal must end the trial with reason `backgrounded`, and `assess` must reject it with `incomplete_trial`. This is a software check, not a bench measure.

## 6. Mountings, run order, and blocking

### 6.1 Mountings

Each device has 6 mountings. One mounting change means all of these steps:

1. Lift the stylus. Unclamp the device. Lift it off the plate.
2. Clean the surface. Place the device again. Shift it by at least 10 mm in x or in y from the previous mounting, within the travel.
3. Clamp. Level within 0.5 degrees. Align within 1 degree (section 6.2).
4. Measure the stylus altitude (section 3.2).
5. Do the camera plane calibration and the marker offset measurement (section 7.2).
6. Run the qualification session (section 8.1).
7. Record the mounting in `bench/registry.json`. The record has the date, the time, the device, the position, the measured angle, and the calibration and qualification results.

A mounting change does not change the stylus, the nib, the load, or the software. If any of these changes, record it as a deviation.

### 6.2 Axis and length check

At every mounting, before the runs:

1. Command a 100 mm line in +x and a 100 mm line in +y with the stylus down.
2. The device length of each line is 100 mm within 0.5 mm. The camera length is 100 mm within 0.5 mm.
3. The device x increases along the +x line, and the device y increases along the +y line.
4. The angle between the device line and the camera line is 1 degree or less.

The only permitted correction is the steps per millimetre of the motion system, before the lock. The device scales come from section 2 and are never adjusted. If a check fails after the lock, stop the block and record a deviation.

### 6.3 Blocks and order

The 12 blocks run in the order W P P W P W W P W P P W. Both devices have the same mean block position.

Each block holds 40 main runs (8 programs × 5 repeats) and 9 fault runs (3 fault programs × 3 repeats) in one random order. The order of block b comes from `random.Random(20261103 + b).shuffle` on the list of 49 run labels in program order, with b from 1 to 12. `bench_programs.py --orders bench/order` writes the 12 order files once. The lock hashes them.

The totals are 480 main runs and 108 fault runs.

### 6.4 Failed runs

A main run fails in three cases. `assess` rejects it. The camera check finds a motion failure (section 7.6). The operator records a `mark` with code `disturbance` or `deviceProblem`. Keep the recording. Run the same program once more at the end of the block. Use the new run only when it passes. Report every failure and every rerun.

A program is estimable for a feature on a device only when every mounting has at least 4 valid runs. Report every program that is not estimable.

### 6.5 Pilot

Before the lock, with 2 mountings per device:

- 2 runs of every main program and 1 run of every fault program per mounting.
- The qualification session of section 8.1 per mounting.
- The axis and length check of section 6.2 per mounting.
- The camera validation of section 7.5 once.

The pilot serves to check the equipment, the software, the run sequence, the executed duration, the fault results, and the camera. It gives the measured sample interval and a rough estimate of the run and mounting variances for section 11.8. Rerun `bench_precision.py` with these estimates before the lock. The pilot feature values do not enter the main analysis, and nobody inspects the device difference of the pilot feature values. Changes after the pilot go into the protocol before the lock.

## 7. Camera

### 7.1 Setup

- A camera that records at 240 frames per second, fixed above the device with its optical axis normal to the surface within 2 degrees.
- The field of view covers the largest frame plus 10 mm on each side. One pixel is 0.1 mm or less in the marker plane.
- The exposure time is 1 ms or less. The lighting is constant direct current light without flicker.
- The marker is a 3 mm matte white dot on a black field, fixed to the holder at height z_m above the surface. Measure z_m with a caliper and record it.
- One video file holds one run.

### 7.2 Calibration

Intrinsic calibration, once before the lock. Take 20 or more views of a printed checkerboard and run OpenCV `calibrateCamera`. Record the reprojection error.

Plane calibration, at every mounting. Place the checkerboard on a spacer of height z_m on the device. Put the board x axis along the device x axis and the board origin on a marked grid point. Find the corners with `findChessboardCorners` and `cornerSubPix`. Remove the lens distortion. Fit a homography from the image to millimetres in the marker plane. The calibration is valid when the residual is 0.03 mm root mean square or less.

Marker offset, at every mounting. Put the stylus tip on the marked grid point. Record a still image and take the marker position. The marker offset is the marker position minus the tip position. The stylus is tilted, so the offset is not zero.

### 7.3 Frame times and tracking

- Read the presentation timestamps of the video with `ffprobe`. The record is valid only when every frame interval is within 1 percent of the median interval plus 1 µs.
- In the first frame, find the marker inside a 10 mm square in the marker plane. The square is centred on the commanded start point plus the marker offset. A bright region counts only when it lies completely inside the square.
- In each later frame, search a window around the previous position. Separate the marker with Otsu thresholding and take the intensity-weighted centroid of the largest bright region.
- Remove the lens distortion from the centroid and map it through the homography. Subtract the marker offset.
- The record is invalid when more than 2 percent of the frames have no marker.

### 7.4 Synchronisation

The camera and the devices share no clock. Each record is analysed on its own:

- The motion start of a camera track is the first frame of 6 consecutive frames with a speed of 2 mm/s or more. The motion end is the last frame of 6 consecutive frames with a speed of 2 mm/s or more. The motion duration is the time between them.
- The motion start and end of a device recording use the same rule on the 200 Hz grid speed of `scriptkin.signal.kinematics`.
- The spatial comparison aligns the tracks in space (section 8.2).

### 7.5 Validation before the lock

1. Static test. The motion system places the marker at 25 commanded grid positions. The root mean square difference is 0.05 mm or less.
2. Speed test. Command a 100 mm line at 20 mm/s and at 40 mm/s. The camera speed agrees with the command within 1 percent.
3. Background test. The tracker finds the marker in every frame of a pilot run on each device. The backgrounds are the lit iPad screen and the Wacom surface.
4. Synthetic test. The tracker reproduces known motion from synthetic frames within the limits above.

### 7.6 Executed movement check

For every run with a valid camera record:

1. Align the camera track to the commanded path of its program by a least-squares translation and rotation.
2. Compute the distance from each camera sample to the nearest point of the commanded path.
3. The run is a motion failure when the largest distance is above 0.2 mm. It is also a motion failure when the motion duration differs from the commanded duration by more than 1 percent.

A motion failure is a failed run (section 6.4). Report the main analysis also without the motion failure exclusions, as a sensitivity analysis.

### 7.7 Missing or invalid camera records

The run stays in the device comparison. The run leaves the accuracy analysis. Report the count per device and program.

## 8. Measures

### 8.1 Sampling

At every mounting, the motion system executes the three qualification tasks of the battery in one qualification session with `purpose` `qualification`. The tasks are `qual-reference` (the 100 mm bar), `qual-hold` (10 s still at the dot), and `qual-line` (the 100 mm line at 20 mm/s). `scriptkin.qualification.qualify` reports these values:

- `intervals` holds the count, the median, the 5th and the 95th percentile of the contact sample intervals, and the share of intervals above 30 ms.
- `hold_jitter_mm` is the root mean square distance of the still samples from their mean.
- `line_deviation_mm` is the root mean square orthogonal distance from the total least squares line.
- `scale`, `planned_channels`, and `observed_channels`.

For every main run, compute the same interval statistics from its contact strokes, plus the share of intervals above `max_gap_s` (0.05 s). Report, per device and program, the median of the run medians, the range of the 5th and 95th percentiles, and the gap shares.

The device P hold is a special case. A still Pencil gives few samples. Report the sample count of the hold with the jitter.

### 8.2 Spatial accuracy

For every main run with a valid camera record, align the device samples to the camera track by a least-squares translation and rotation. Report the fitted rotation. A rotation above 1 degree is a deviation. Then compute the distance from each device sample to the nearest point of the camera polyline. Report the root mean square and the 95th percentile per device and program.

For every main run, compute `spatial.trace_rmse` against the commanded path of its program (the scaled task paths). The library computes it for trace tasks. For the loops copy task, compute the same nearest-distance statistic after the alignment above.

Compare the device speed with the camera speed. Process the camera track as a recording with the frame interval as its sample interval and `spatial_unit` `mm`. Compute `kinematic.speed_mean` with the same code. Report the device-minus-camera difference on the logarithmic scale per device and program, with a 90 percent interval from the mounting means of the run differences.

### 8.3 Feature agreement

Section 9 gives the features, section 10 the limits, and section 11 the statistics.

## 9. Feature set

### 9.1 Features that the library computes

The names and definitions are those of `scriptkin.features.CATALOG` and `scriptkin.signal`. Features are computed per accepted contact segment on the 200 Hz grid and pooled with the catalog rule.

| Feature | Unit | Definition in the code | Comparison scale |
| --- | --- | --- | --- |
| `timing.contact_s` | s | Sum of accepted contact segment durations. | ln |
| `kinematic.path_length` | mm | Sum of chord lengths between consecutive raw samples of the contact segments. | ln |
| `kinematic.speed_mean` | mm/s | Mean of the Savitzky-Golay speed on the 200 Hz grid, mean over segments. | ln |
| `spectral.speed_band_power` | (mm/s)² | Welch power of the grid speed in the 4 to 12 Hz band, 2 s windows with half overlap, at least 3 windows, trapezoid integration. | ln |

The plan names "duration". The bench uses `timing.contact_s`, because `timing.duration_s` includes the operator's taps. The plan names "tremor-band power". The bench uses the 4 to 12 Hz band of the code.

### 9.2 Features that the bench adds

The library does not compute these three features. The build phase adds them to a bench module of `scriptkin` before the lock, with these exact definitions. Each uses the accepted contact segments resampled by `scriptkin.signal.resample` at 200 Hz, and the `Kinematics` of `scriptkin.signal.kinematics`.

`bench.loop_height_mm`, unit mm, comparison scale ln, loops task only:

1. Concatenate the grid y values of the contact segments in time order.
2. The baseline points are the first grid sample, the interior local maxima of y, and the last grid sample. Find the maxima with `scipy.signal.find_peaks(y, prominence=0.25 × (p95 − p5), distance=50)`. p95 and p5 are the 95th and 5th percentiles of y with linear interpolation. The distance of 50 samples is 0.25 s.
3. Loop j runs from baseline point j (included) to baseline point j + 1 (excluded). N is the number of loops.
4. The height of loop j equals the mean y of its two baseline points minus its minimum y.
5. The feature is the median height of the interior loops 2 to N − 1. With N below 4, the feature is a refusal with code `undefined`.

The commanded height is 20 mm at scale 1.0 and 10 mm at scale 0.5.

`bench.speed_cv`, unit 1, comparison scale absolute. Concatenate the grid speed of all contact segments. The feature is its standard deviation with divisor n − 1 divided by its mean. With fewer than 2 grid samples, the feature is a refusal with code `too_few_values`.

`bench.ldlj`, unit 1, comparison scale absolute. For each contact segment with a `Kinematics` result, let D be the segment duration and S the sum of the grid chord lengths. Let J be the trapezoid integral over the grid times of the squared norm of the jerk. The segment value is ln(D⁵ J / S²). The feature is the mean over segments. The name follows the "LDLJ" of reference R3.

### 9.3 Descriptive features

Report these without limits: `timing.stroke_count`, `timing.in_air_s`, `kinematic.speed_max`, `kinematic.acceleration_mean`, `kinematic.jerk_mean`, `pressure.force_mean`, `pressure.force_cv`, `tilt.altitude_mean`, `tilt.azimuth_variance`, `spatial.trace_rmse`, `spatial.copy_rmse`, `entropy.speed_sample_entropy`, `micro.spike_mean`, and the `micro.gamma_*` estimates. Pressure is descriptive because the bench does not calibrate force.

## 10. Equivalence limits

The limits follow one rule. The limit is one standard error of measurement (SEM) of the feature on the comparison scale. The SEM comes from the closest published human test-retest study on a touchscreen or tablet task. A device bias at or below one SEM is smaller than the measurement noise of one human test. Such a bias cannot be told apart from retest noise. Where only a minimal detectable change (MDC) is published, SEM = MDC / 2.77. Where no source exists, the table states the rule and marks the limit as a judgement for review.

| Feature | Scale | Limit L | Source value | Status |
| --- | --- | --- | --- | --- |
| `timing.contact_s` | ln | ± 0.046 | Movement time SEM 4.7 percent (reference R1, a reaching task on a 120 Hz touchscreen in 64 people after stroke). ln(1.047) = 0.046. | Source, related task |
| `kinematic.path_length` | ln | ± 0.011 | Trajectory length SEM 1.1 percent (R1). ln(1.011) = 0.011. | Source, related task |
| `kinematic.speed_mean` | ln | ± 0.042 | Mean speed SEM 4.3 percent (R1). ln(1.043) = 0.042. | Source, related task |
| `bench.loop_height_mm` | ln | ± 0.025 | No published SEM. The rule is the stated accuracy of a Wacom tablet, ± 0.25 mm for the Intuos 3 as reported in reference R2, over the smaller commanded height of 10 mm. ln(1.025) = 0.025. Reference R4 reports an ICC of 0.75 for the vertical size of handwriting, without an SEM. | Judgement |
| `bench.speed_cv` | absolute | ± 0.028 | No published SEM. The rule is one tenth of the designed speed CV of the loops programs (0.281), the rule of protocol 4.8 for a designed contrast. | Judgement |
| `bench.ldlj` | absolute | ± 0.35 | Log dimensionless jerk SEM 2.2 percent (R1), applied to the commanded LDLJ of the loops programs (16.1): 0.35. | Judgement, source-informed |
| `spectral.speed_band_power` | ln | ± 0.52 | Tablet spiral tremor amplitude, test-retest standard deviation of differences 0.16 on the log10 scale, 18 people with essential tremor (R2). SEM = 0.16 / √2 = 0.113 log10 = 0.26 ln on amplitude. Power scales with the square of amplitude, so the limit is 0.52 ln. | Source, derived |

All limits are inclusive. They are fixed at the lock and never changed after a result. Every result is reported, including a failed limit.

## 11. Statistics

### 11.1 Comparison scales

For `timing.contact_s`, `kinematic.path_length`, `kinematic.speed_mean`, `bench.loop_height_mm`, and `spectral.speed_band_power`, the analysis uses the natural logarithm of the value. A bias of 0.046 on this scale is a ratio of 1.047. For `bench.speed_cv` and `bench.ldlj`, the analysis uses the value itself.

### 11.2 Device difference with mounting effects

For each feature and program:

1. The mounting mean is the mean over the valid runs of the program in one mounting, on the comparison scale. Each device has 6 mounting means.
2. The bias is the mean of the device P mounting means minus the mean of the device W mounting means.
3. The 90 percent confidence interval is the Welch interval on the two sets of 6 mounting means. It is bias ± t × √(s²_P / 6 + s²_W / 6). s²_P and s²_W are the variances of the mounting means. t is the 95th percentile of the t distribution with Welch-Satterthwaite degrees of freedom. When both variances are zero, the degrees of freedom are 10.

No variance is pooled across programs. The interval includes the mounting effects because the mounting means carry them.

The variance components per device and program come from a one-way analysis of variance of the runs with mounting as the factor. σ²_run = MS_within. σ²_mount = max(0, (MS_between − MS_within) / n̄). n̄ is the mean number of valid runs per mounting.

### 11.3 Equivalence test

A feature is equivalent for a program when the 90 percent interval of section 11.2 lies inside [−L, L]. This is the two one-sided tests procedure at α = 0.05 per side. A feature is bench-equivalent when it is equivalent for every program. Report the decision per program and the overall decision.

### 11.4 Bland-Altman with repeated measures

Pair the mountings of the two devices by their index, 1 to 6, in mounting order. For each feature, program p, and pair i, the difference d_pi is the device P mounting mean minus the device W mounting mean. The average a_pi is the mean of the two mounting means. The Bland-Altman plot shows d_pi against a_pi for the 48 points.

The limits of agreement for a mounting mean are d̄ ± 1.96 × s_d. d̄ is the mean of all d_pi. s_d² = σ²_within + σ²_between, from a one-way analysis of variance of d_pi with program as the factor. σ²_within = MS_within. σ²_between = max(0, (MS_between − MS_within) / 6).

The limits of agreement for a single run add the run variances. s²_single = s_d² + (4 / 5) × (σ²_run,P + σ²_run,W). The run variances are pooled across programs.

### 11.5 Deming correction with leave-one-mounting-out validation

Fit a Deming regression for every feature. For a feature that is not bench-equivalent, the fit is the correction. For every other feature, the fit is a report. The regression is of the device P mounting means on the device W mounting means, with the 48 pairs of section 11.4. The error variance ratio λ is (σ²_mount,P + σ²_run,P / 5) / (σ²_mount,W + σ²_run,W / 5) from the pooled variance components. When a variance is zero, λ is 1. The slope is β = (S_yy − λ S_xx + √((S_yy − λ S_xx)² + 4 λ S_xy²)) / (2 S_xy). The intercept is α = ȳ − β x̄.

Validation. For each mounting index k from 1 to 6, fit the regression on the 40 pairs of the other five mountings. Apply it to the device W mounting means of mounting k. The residual of a program is its device P mounting mean of mounting k minus the corrected value. For each program, the residual bias is the mean over the 6 held-out mountings. Its 90 percent t interval comes from their standard deviation with 5 degrees of freedom. The correction passes for a program when this interval lies inside [−L, L].

The simulation of section 11.8 shows that the mean of the held-out residuals cancels the mounting effects. Every fold subtracts the mean of the other mountings. The spread of the residuals over the folds carries the mounting variation, and so does the interval. Report both.

The published correction is the fit on all 48 pairs, with standard errors from a bootstrap over the 6 mounting pairs (2000 draws, `numpy.random.default_rng(20261103)`).

### 11.6 Sampling and accuracy summaries

Report the measures of section 8 per device and program as medians with ranges over runs. These measures have no acceptance limit.

### 11.7 Quality gate measures

- The share of main runs that `assess` accepts, per device, with an exact 95 percent Clopper-Pearson interval.
- The detection fraction per fault program and device (section 5.3), with the same interval. With 18 of 18 detected, the lower bound is 0.815.

### 11.8 Precision

`python/studies/bench_precision.py` simulates the design. Run it from the repository root:

```sh
UV_PROJECT_ENVIRONMENT=<venv folder> uv run --project python --frozen \
    python -I python/studies/bench_precision.py --datasets 20000 --seed 20261103
```

The seed 20261103 spawns one child seed per cell through `numpy.random.SeedSequence`. The script writes `studies/bench_precision.json` into the build folder of `./check` and prints the tables below. All values are in units of the limit L, so one table serves every feature.

In the model, a run value is the device mean plus a mounting effect plus run noise. The mounting effect has standard deviation σ_mount and is shared by the runs of the mounting. The run noise has standard deviation σ_run. The analysis is that of sections 11.2 and 11.3.

The assumed variance components have this rationale. A robot repeats a run far better than a person. So σ_run is a fraction of a human SEM, and the SEM is the limit. The grid covers σ_run of 0.1, 0.25, and 0.5 L. A remount can shift a feature by a comparable fraction. The grid covers σ_mount of 0, 0.1, 0.25, and 0.5 L. The pilot measures both. The script runs again with the measured values before the lock.

Results for 6 mountings × 5 runs, share of 20000 datasets declared equivalent:

| σ_run | σ_mount | Median half-width | Bias 0 | Bias 0.5 L | Bias L |
| --- | --- | --- | --- | --- | --- |
| 0.25 | 0.10 | 0.154 | 1.000 | 1.000 | 0.048 |
| 0.25 | 0.25 | 0.282 | 1.000 | 0.895 | 0.048 |
| 0.25 | 0.50 | 0.526 | 0.860 | 0.449 | 0.050 |
| 0.50 | 0.25 | 0.346 | 0.999 | 0.771 | 0.047 |
| 0.50 | 0.50 | 0.564 | 0.786 | 0.413 | 0.049 |

The other designs at σ_run 0.25 L and σ_mount 0.25 L:

| Design | Bias 0 | Bias 0.5 L |
| --- | --- | --- |
| 3 × 5 | 0.911 | 0.508 |
| 6 × 3 | 1.000 | 0.867 |
| 6 × 5 | 1.000 | 0.895 |
| 8 × 5 | 1.000 | 0.965 |
| 6 × 8 | 1.000 | 0.907 |

The share at a bias of L is 0.037 to 0.052 in every cell, so the test keeps its 5 percent error.

The conclusion follows. Take σ_mount at or below 0.25 L and σ_run at or below 0.5 L. Then 6 mountings × 5 runs declare an unbiased feature equivalent in at least 0.999 of datasets. They declare a feature with a bias of half the limit equivalent in at least 0.77 of datasets. More runs per mounting add little, because the mounting variance limits the precision. Two more mountings would add 0.07 at a bias of 0.5 L for a third more work. The design needs σ_mount at or below 0.25 L. The table gives this bound per feature. If the pilot shows a larger mounting variance for a feature, section 13 applies.

| Feature | σ_mount bound (0.25 L) |
| --- | --- |
| `timing.contact_s` | 1.2 percent |
| `kinematic.path_length` | 0.28 percent |
| `kinematic.speed_mean` | 1.05 percent |
| `bench.loop_height_mm` | 0.62 percent |
| `bench.speed_cv` | 0.007 |
| `bench.ldlj` | 0.09 |
| `spectral.speed_band_power` | 0.13 on the logarithmic scale |

The correction table of the same run uses a true relation with an intercept of 0.5 L and a slope of 1.05. Its uncorrected bias reaches 0.9 L. Take 6 × 5, σ_run 0.25 L, and σ_mount up to 0.25 L. Then the held-out residual interval lies inside the limit for all eight programs in 1.000 of datasets. At σ_mount 0.5 L the share is 0.769. With 3 × 5 the best share is 0.877.

### 11.9 Sensitivity analyses

Report the analysis of sections 11.2 to 11.5 also:

- without the motion failure exclusions of section 7.6,
- per task, per scale, and per speed, pooled over the other factors,
- with the mounting means replaced by the medians of the runs.

## 12. Deliverables for the consequence experiment

The bench delivers `bench/results/deliverables.json` with, for each feature and program:

- The bias on the comparison scale, its standard error, and its 90 percent interval.
- The variance components σ²_run and σ²_mount for each device.
- The correction α and β with their bootstrap standard errors, and the held-out residual bias with its interval.
- The standardised difference, which is the bias divided by √((σ²_P + σ²_W) / 2), with σ² = σ²_mount + σ²_run per device.

The deliverables also give each difference in a scale-free form. For the logarithmic features, the bias is a multiplicative factor exp(bias) that applies in any unit. The consequence experiment and the ParkinsonHW example of the plan can apply it to a feature whose unit is unknown. `bench.speed_cv` and `bench.ldlj` are dimensionless and need no scale. `timing.contact_s` is in seconds, which the public files have. The standardised difference applies to any feature in its own units. The deliverables state which form each feature supports.

## 13. Stop rules, deviations, and the deviation log

Stop before the lock, and do not lock, when any of these holds:

- The camera validation (section 7.5) or the motion qualification (section 4.2) fails.
- The axis and length check (section 6.2) fails on a device after the steps per millimetre correction.
- The parity test (section 2.4) fails.
- The pilot median sample interval of a device is above 25 ms (40 Hz, `min_rate_hz`).
- The pilot estimate of σ_mount for a feature is above 0.5 L. Then find and remove the cause of the mounting variation, or raise the planned mountings and rerun section 11.8. Record the change in the protocol before the lock.

Stop a block after the lock, remount, and record a deviation when any of these holds:

- The qualification session of the mounting shows a hold jitter above twice the pilot median, or a line deviation above 0.1 mm.
- The axis and length check fails.
- Three consecutive runs fail.
- The stylus altitude or the load is outside its tolerance.

A deviation is any departure from this protocol after the lock. Record every deviation as a table row in `bench/DEVIATIONS.md`. The row has the date and time, the runs affected, the departure, the cause, the decision, and the person. Analysis changes after the lock are deviations and are reported in the paper as post hoc.

## 14. Lock procedure

The lock registers everything that the main runs depend on. Do it after the pilot and before the first main run.

1. Complete the parity test, the camera validation, the motion qualification, and the pilot.
2. Rerun `bench_precision.py` with the pilot variances. Run `./check` at the lock commit. Both must pass.
3. Put the pilot measurements and the chosen constants into `bench/registry.json`. Copy the precision report to `bench/results/bench_precision.json`.
4. Generate the run orders (section 6.3) and the program files (section 5.1).
5. Write `bench/BENCH_LOCK.json`. It holds the protocol version, the repository commit hash, and the SHA-256 of each file in this list:
   - `docs/BENCH_PROTOCOL.md`
   - `bench/registry.json`
   - every file under `bench/programs/` and `bench/order/`
   - `python/uv.lock`
   - `python/studies/bench_programs.py` and `python/studies/bench_precision.py`
   - the precision report `bench/results/bench_precision.json`

   The commit hash covers the analysis code.
6. Commit the lock file. The commit hash is the lock reference.
7. Write the commit hash and the SHA-256 of `bench/BENCH_LOCK.json` as the first row of `bench/DEVIATIONS.md`.
8. An independent reviewer checks the lock file against the files and the commit before the first main run. The reviewer records the check as the second row of `bench/DEVIATIONS.md`.

The lock file does not contain its own digest. The paper and the supplement cite the lock commit.

## 15. Data and file naming

All bench files live under `bench/` in the repository. The recordings and the video are large and live in the study store of the records drive. The study store path is in `bench/registry.json`.

| Path | Content |
| --- | --- |
| `bench/registry.json` | Devices, software versions, fixture measurements, camera and motion settings, one record per mounting, pilot measurements, constants. |
| `bench/programs/<program>.json` | The move list of each program, from `bench_programs.py --write`. |
| `bench/programs/<program>.<controller>` | The controller file generated from the move list by the translation script. |
| `bench/order/block-<bb>.csv` | The run order of block bb (01 to 12), with position, program, and repeat, from `bench_programs.py --orders`. |
| `bench/runs.csv` | One row per run, with run id, device, mounting, block, position, program, repeat, session id, trial index, camera file, status, and deviation row. |
| `bench/DEVIATIONS.md` | The deviation log. |
| `bench/BENCH_LOCK.json` | The lock file. |
| `bench/results/` | The analysis outputs: `features.csv` in the `FeatureTable` format with its provenance file, `agreement.csv`, `bland_altman.csv`, `correction.csv`, `sampling.csv`, `accuracy.csv`, `gate.csv`, `bench_precision.json`, and `deliverables.json`. |

Run ids are `<device>-m<mounting>-<program>-r<repeat>`, for example `P-m3-loops-s05-v40-r2`.

Each block is one journal session. The capture tool exports it as `SCRIPT_BENCH_<device><mounting>_<session8>/`. Each run is one trial, in block order. `trialStarted.bench` names the program and the run index (section 2.4).

The camera files are `<run id>.mov`, `<run id>.frames.json` from `ffprobe`, and `<run id>.track.csv` from the tracker. They live under `camera/<device>-m<mounting>/` in the study store.

The analysis reads `bench/runs.csv`, the journals, and the tracks, and writes `bench/results/`. One command does this. The build phase names it, and the lock registers it.

## 16. What the paper may and may not claim

The paper may claim:

- For each feature and program, whether the two measurement chains are equivalent within the registered limit. The claim holds for the tested units, software versions, surfaces, mountings, stylus angle, load, and speeds.
- The size of the bias and its interval, the limits of agreement, and the residual bias after the correction on held-out mountings.
- The sample interval, the gap share, the position noise, and the spatial accuracy of each device against the camera and the command.
- Which fault programs the quality checks detect on each device.
- The consequence of the measured bias and noise in the synthetic cohorts, as the plan defines.

The paper may not claim:

- Anything about people, their handwriting, their health, or any mental health condition.
- That the devices agree for human movement. The motion system is not a person. Glass and the tablet surface change human movement, and the bench cannot measure that.
- Agreement outside the tested speeds (mean 20 and 40 mm/s), sizes, angle, load, or units.
- Agreement of pressure, tilt, or hover. These are descriptive here.
- Equivalence from a failed limit, or a limit changed after a result.
- A report rate for the Wacom tablet other than the measured one.

## References

Each reference was opened on 3 November 2026 at the address given.

- R1. Goizueta S, Navarro MD, Calvo G, Campos G, Colomer C, Noé E, Llorens R. Touchscreen-based assessment of upper limb kinematics after stroke: reliability, validity and sensitivity to motor impairment. Journal of NeuroEngineering and Rehabilitation. 2025;22:27. DOI 10.1186/s12984-025-01563-6. Opened at https://pmc.ncbi.nlm.nih.gov/articles/PMC11817959/. Values used. SEM 4.7 percent for movement time, 1.1 percent for trajectory length, and 4.3 percent for mean speed. SEM 2.2 percent for the log normalised dimensionless squared jerk. The task was reaching on a 120 Hz touchscreen, trials 2 and 3, in 64 people after stroke.
- R2. Elble RJ, Ellenbogen A. Digitizing tablet and Fahn-Tolosa-Marín ratings of Archimedes spirals have comparable minimum detectable change in essential tremor. Tremor and Other Hyperkinetic Movements. 2017;7:481. DOI 10.5334/tohm.344. Opened at https://tremorjournal.org/articles/344. Values used. The device was a Wacom Intuos 3 with a stated accuracy of ± 0.25 mm at 100 samples per second. The tablet tremor amplitude had an ICC of 0.97 (0.91 to 0.99). The standard deviation of the log10 test-retest differences was 0.16. The minimal detectable change was 51 percent of the baseline geometric mean. The study had 18 people on two consecutive days.
- R3. Toffoli S, Abbate C, Lunardini F, Corno E, Diani N, Gallucci A, Tomasini E, Trimarchi PD, Ferrante S. Handwriting in mild cognitive impairment: reliability assessment and machine learning-based screening. JMIR Aging. 2025;8:e73074. DOI 10.2196/73074. Opened at https://pmc.ncbi.nlm.nih.gov/articles/PMC12504891/. Values used. The name LDLJ, and its test-retest reliability of 0.89 to 0.93 for cursive tasks with a sensorised ink pen at 50 Hz. No SEM is reported.
- R4. Bisio A, Pedullà L, Bonzano L, Ruggeri P, Brichetto G, Bove M. Evaluation of handwriting movement kinematics: from an ecological to a magnetic resonance environment. Frontiers in Human Neuroscience. 2016;10:488. DOI 10.3389/fnhum.2016.00488. Opened at https://pmc.ncbi.nlm.nih.gov/articles/PMC5040726/. Values used. Test-retest ICC 0.74 for sentence duration and 0.75 for vertical size, in 22 adults one month apart. No SEM is reported.
- R5. Sisti JA, Christophe B, Seville AR, Garton ALA, Gupta VP, Bandin AJ, Yu Q, Pullman SL. Computerized spiral analysis using the iPad. Journal of Neuroscience Methods. 2017;275:50-54. DOI 10.1016/j.jneumeth.2016.11.004. Opened at PubMed, PMID 27840146. Prior work. An iPad with finger or capacitive stylus against a Wacom tablet, without an Apple Pencil.
- W1. Wacom. Wacom Intuos Pro (2025) product page. Opened at https://www.wacom.com/en-us/products/pen-tablets/wacom-intuos-pro. Values used in section 2.2.
- M1. Evil Mad Scientist. AxiDraw V3/B6 product page. Opened at https://shop.evilmadscientist.com/945. Values used in section 4.3.
- M2. Creality. Ender-3 V3, V3 Plus, and V3 KE technical specifications. Opened at https://www.creality3dofficial.com/blogs/null/exploring-the-ender-3-v3-plus-3d-printer. Values used in section 4.3.
- Protocol 4.8 and its reference implementation, `Screening_Device/BENCH_PROTOCOL.md` and `Screening_Device/bench_reference/` in the earlier project folder. Reused: the fixture and holder design, the camera method, the axis and length check, the lock procedure, and the designed contrast rule.
