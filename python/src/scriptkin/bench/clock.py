"""The clock LED, the frame clock, and the row readout of protocol section 7.3.

The LED level of a frame is the mean of the LED region over its pixels and channels. A clip decides the
LED state with one threshold: the Otsu threshold of its LED levels. The LED is on in a frame when the
level is above the threshold. A clip whose two Otsu classes differ by less than `LED_MIN_CONTRAST` grey
levels shows no LED change, and every frame reports the LED off. The frame clock then finds no period,
and the analysis finds no LED period in the clip.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np

from scriptkin.bench.camerafiles import CLOCK_TOLERANCE, MIN_CLOCK_PERIODS, Clock, Frame, Readout, Region
from scriptkin.bench.track import BoolArray, FrameTimes, ReadoutDirection
from scriptkin.quality import Refusal, RefusalCode
from scriptkin.recording import FloatArray

LED_MIN_CONTRAST = 32.0
BAND_MIN_CONTRAST = 32.0
MIN_BAND_FRAMES = 10
MAX_BAND_PITCH_SPREAD = 0.01
MIN_BAND_STEP = 0.05
"""The smallest band movement per frame, in pitches, from 0 and from half a pitch, that shows the
readout direction."""
_EDGE_SPACING_TOLERANCE = 0.05


def led_level(frame: Frame, region: Region) -> float:
    return float(region.crop(frame).mean())


def led_levels(frames: Iterable[Frame], region: Region) -> FloatArray:
    return np.array([led_level(f, region) for f in frames], dtype=np.float64)


def _otsu(values: FloatArray) -> tuple[float, float]:
    """The Otsu threshold of the values, and the difference of the means of its two classes."""
    v = np.sort(values)
    n = len(v)
    if n < 2:
        return float("inf"), 0.0
    below = np.cumsum(v)[:-1]
    k = np.arange(1, n)
    low = below / k
    high = (v.sum() - below) / (n - k)
    best = int(np.argmax(k * (n - k) * (high - low) ** 2))
    return float(v[best] + v[best + 1]) / 2, float(high[best] - low[best])


def led_states(levels: FloatArray) -> BoolArray:
    threshold, contrast = _otsu(levels)
    if contrast < LED_MIN_CONTRAST:
        return np.zeros(len(levels), dtype=bool)
    return levels > threshold


def measure_clock(
    levels: FloatArray, pts_s: FloatArray, *, led_hz: float, nominal_hz: float
) -> Clock | Refusal:
    """The frame clock from the LED levels and the presentation times of one clip of the LED.

    The rate is the least-squares slope of the rising-edge frames against the LED period number, times
    the LED rate. Its resolution is about one frame over the clip, 0.007 percent for 60 s, because an
    edge is the first frame with the LED on. The presentation times are capture times when their slope
    at the same edges agrees with the LED period within `CLOCK_TOLERANCE`. A clip with an edge spacing
    that is not a whole number of nominal periods, within 5 percent, has no periodic LED and is refused.
    """
    if len(levels) != len(pts_s):
        raise ValueError("Each frame needs one LED level and one presentation time.")
    on = led_states(levels)
    rising = np.flatnonzero(on[1:] & ~on[:-1]) + 1
    if len(rising) < 2:
        return Refusal(RefusalCode.TOO_FEW_VALUES, "The clip shows fewer than two rising edges of the LED.")
    nominal_period = nominal_hz / led_hz
    spacing = np.diff(rising)
    periods = np.rint(spacing / nominal_period)
    if np.any(periods < 1) or np.any(
        np.abs(spacing - periods * nominal_period) > _EDGE_SPACING_TOLERANCE * nominal_period
    ):
        return Refusal(
            RefusalCode.OUT_OF_RANGE, "The LED edges are not a whole number of nominal periods apart."
        )
    number = np.concatenate(([0.0], np.cumsum(periods)))
    if number[-1] < MIN_CLOCK_PERIODS:
        return Refusal(
            RefusalCode.TOO_FEW_VALUES,
            f"The clip holds {number[-1]:.0f} LED periods. It needs {MIN_CLOCK_PERIODS}.",
        )
    rate = float(np.polyfit(number, rising.astype(np.float64), 1)[0]) * led_hz
    if abs(rate / nominal_hz - 1) > CLOCK_TOLERANCE:
        return Refusal(
            RefusalCode.OUT_OF_RANGE,
            f"The measured rate is {rate:.4f} Hz, more than {CLOCK_TOLERANCE:.1%} from {nominal_hz:g} Hz.",
        )
    ratio = float(np.polyfit(number, pts_s[rising], 1)[0]) * led_hz
    frame_times = FrameTimes.CAPTURE if abs(ratio - 1) <= CLOCK_TOLERANCE else FrameTimes.PLAYBACK
    return Clock(rate, frame_times, ratio, int(number[-1]), led_hz, nominal_hz)


def _crossings(profile: FloatArray, level: float, rising: bool) -> FloatArray:
    above = profile > level
    i = np.flatnonzero(above[1:] & ~above[:-1] if rising else ~above[1:] & above[:-1])
    return i + (level - profile[i]) / (profile[i + 1] - profile[i])


def _band_shift(before: FloatArray, after: FloatArray, pitch: float) -> float:
    """The movement of the band edges from one frame to the next, in pitches from -0.5 to 0.5."""
    phase = [np.mean(np.exp(2j * np.pi * edges / pitch)) for edges in (before, after)]
    return float(np.angle(phase[1] / phase[0])) / (2 * np.pi)


def _readout_direction(
    edges: Mapping[int, tuple[FloatArray, FloatArray]], pitch: float, *, led_hz: float, nominal_hz: float
) -> ReadoutDirection | Refusal:
    step = (led_hz / nominal_hz) % 1.0
    if min(step, abs(step - 0.5), 1.0 - step) < MIN_BAND_STEP:
        return Refusal(
            RefusalCode.OUT_OF_RANGE,
            f"At {nominal_hz:g} frames per second, the bands of the {led_hz:g} Hz LED do not show the "
            "readout direction.",
        )
    # A top-first readout moves the bands by -step pitches per frame. Wrapped to half a pitch, that is
    # negative for a step below 0.5 and positive above it.
    top_sign = 1.0 if step > 0.5 else -1.0
    votes = {
        float(np.sign(_band_shift(a, b, pitch)) * top_sign)
        for index, kinds in edges.items()
        if index + 1 in edges
        for a, b in zip(kinds, edges[index + 1], strict=True)
        if len(a) and len(b)
    }
    if votes == {1.0}:
        return ReadoutDirection.TOP_FIRST
    if votes == {-1.0}:
        return ReadoutDirection.BOTTOM_FIRST
    return Refusal(
        RefusalCode.UNDEFINED,
        "The bands of consecutive frames do not move one way, so the readout direction is not known.",
    )


def measure_readout(
    frames: Iterable[Frame], region: Region, *, led_hz: float, nominal_hz: float
) -> Readout | Refusal:
    """The row time and the readout direction from a clip of the LED at `led_hz`, which a rolling shutter
    shows as bands.

    Each frame gives a row profile: the mean of each row of the region. The band edges are the sub-row
    crossings of the midpoint between the 5th and the 95th percentile of the profile. The band pitch is
    the summed span between the first and the last edge of one kind, over every frame and both kinds,
    divided by the summed number of pitches. The row time is the LED period divided by the band pitch.

    From one frame to the next, the LED phase advances by `led_hz / nominal_hz` periods. A top-first
    readout moves the bands by that phase towards row 0, and a bottom-first readout moves them the other
    way (protocol section 7.3). The direction is the one that every pair of consecutive frames with bands
    shows.
    """
    edges: dict[int, tuple[FloatArray, FloatArray]] = {}
    for index, frame in enumerate(frames):
        profile = region.crop(frame).reshape(region.height, -1).mean(axis=1)
        low, high = np.percentile(profile, [5, 95])
        if high - low < BAND_MIN_CONTRAST:
            continue
        kinds = (
            _crossings(profile, float(low + high) / 2, True),
            _crossings(profile, float(low + high) / 2, False),
        )
        if any(len(e) >= 2 for e in kinds):
            edges[index] = kinds
    spans = [(float(e[-1] - e[0]), len(e) - 1) for kinds in edges.values() for e in kinds if len(e) >= 2]
    if len(edges) < MIN_BAND_FRAMES:
        return Refusal(
            RefusalCode.TOO_FEW_VALUES,
            f"Fewer than {MIN_BAND_FRAMES} frames show two band edges of one kind. A global shutter shows no "
            "bands, and an exposure as long as the LED period hides them.",
        )
    pitch = sum(span for span, _ in spans) / sum(count for _, count in spans)
    if float(np.std([span / count for span, count in spans])) / pitch > MAX_BAND_PITCH_SPREAD:
        return Refusal(
            RefusalCode.OUT_OF_RANGE, "The band pitch varies by more than 1 percent between frames."
        )
    direction = _readout_direction(edges, pitch, led_hz=led_hz, nominal_hz=nominal_hz)
    if isinstance(direction, Refusal):
        return direction
    return Readout(1 / (led_hz * pitch), direction, pitch, led_hz, len(edges))
