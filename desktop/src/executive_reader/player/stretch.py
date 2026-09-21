"""Make speech faster without making it worse.

Neural voices take a speed as a synthesis input, which is the right way to do
it at modest speeds: the model shortens the sounds the way a person speaking
quickly would, and the pitch stays where it belongs.

It stops being the right way further up. Measured on Kokoro with the shipped
voice, asking for more speed returns steadily less of it, and what does arrive
is squashed unevenly:

    asked    1.0    1.5    2.0    2.5    3.0
    got      1.0    1.51   1.78   1.96   2.09

So 3x is really 2.09x, and it is slurred, because the model was never trained
on durations that short. Both problems come from pushing one control past the
range it was fitted for.

This module supplies the rest. The voice is synthesized at a speed it handles
well and the remainder is taken out of the audio afterwards, by overlapping and
adding short windows rather than by resampling. Resampling is what makes fast
speech sound like a chipmunk: it moves every frequency up. Overlap-add leaves
the frequencies alone and removes time instead, which is what a listener
actually wants from a speed control.

The search step is what separates this from plain overlap-add. Each window is
nudged to where it best lines up with what has already been written, which
keeps successive pitch periods in phase; without it the seams buzz.
"""
from __future__ import annotations

import numpy as np

#: Window length in seconds. Long enough to hold a pitch period of a low voice,
#: short enough that a consonant is not smeared across the join.
_WINDOW = 0.040

#: How far to look for a better alignment, in seconds. About one pitch period
#: of a low male voice, which is the longest that needs correcting.
_SEARCH = 0.010

#: Below this the speed change is inaudible and not worth the work.
_DEADBAND = 0.02


def time_stretch(samples: np.ndarray, rate: int, speed: float) -> np.ndarray:
    """Return `samples` sped up by `speed`, at the same pitch.

    speed > 1 shortens, speed < 1 lengthens. The pitch is untouched, which is
    the entire point: this exists so that speeding up does not also transpose.
    """
    if samples is None or len(samples) == 0:
        return np.zeros(0, dtype=np.float32)
    if abs(speed - 1.0) < _DEADBAND or speed <= 0:
        return np.asarray(samples, dtype=np.float32)

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    window = max(64, int(_WINDOW * rate))
    search = max(1, int(_SEARCH * rate))
    hop_out = window // 2
    hop_in = int(round(hop_out * speed))
    if hop_in <= 0:
        return audio

    fade = np.hanning(2 * hop_out).astype(np.float32)
    fade_in, fade_out = fade[:hop_out], fade[hop_out:]

    out = np.zeros(int(len(audio) / speed) + window + hop_out, dtype=np.float32)
    out[:window] = audio[:window] if len(audio) >= window else np.pad(
        audio, (0, window - len(audio)))

    read, write = hop_in, hop_out
    while read + window + search < len(audio) and write + window < len(out):
        # The tail already written is what the next window has to agree with.
        tail = out[write:write + hop_out]
        offset = _best_offset(audio, read, hop_out, search, tail)
        piece = audio[read + offset:read + offset + window]
        if len(piece) < window:
            break
        out[write:write + hop_out] = (tail * fade_out + piece[:hop_out] * fade_in)
        out[write + hop_out:write + window] = piece[hop_out:]
        read += hop_in
        write += hop_out

    written = min(write + window, len(out))
    return out[:written]


def _best_offset(audio: np.ndarray, read: int, length: int,
                 search: int, tail: np.ndarray) -> int:
    """Where near `read` the next window lines up best with what came before.

    Plain overlap-add takes the window as it falls, which puts pitch periods
    out of phase at every join and buzzes. Correlating against the tail and
    shifting by up to one period removes that.
    """
    if len(tail) < length:
        return 0
    window = audio[read:read + length + search]
    if len(window) < length + search:
        return 0
    best, best_score = 0, -np.inf
    # A coarse step: single-sample precision buys nothing audible and costs a
    # great deal, since this runs for every window of every sentence.
    for offset in range(0, search, max(1, search // 12)):
        candidate = window[offset:offset + length]
        score = float(np.dot(candidate, tail))
        if score > best_score:
            best, best_score = offset, score
    return best
