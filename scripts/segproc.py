"""Segmented processing with crossfade, streamed to disk so memory stays flat on hour-long files.

run_segmented(inp, out, process, out_sr): `process(x_float32_mono, in_sr)` must return float32 mono audio
at `out_sr` covering the same time span as its input.
"""
import numpy as np
import soundfile as sf


def run_segmented(inp, out, process, out_sr, seg=90.0, ov=1.0, log=print, start=0.0, dur=None, subtype='FLOAT'):
    info = sf.info(inp)
    sr = info.samplerate
    s0 = int(start * sr)
    s1 = info.frames if dur is None else min(info.frames, s0 + int(dur * sr))
    seg_n, ov_n = int(seg * sr), int(ov * sr)
    ov_o = int(round(ov * out_sr))
    fade_in = (np.sin(np.linspace(0, np.pi / 2, ov_o, endpoint=False)) ** 2).astype(np.float32)  # sin²/cos² crossfade
    fade_out = 1 - fade_in
    tail = None
    pos, i = s0, 0
    with sf.SoundFile(out, 'w', samplerate=out_sr, channels=1, subtype=subtype) as fo:
        while pos < s1:
            end = min(s1, pos + seg_n)
            x, _ = sf.read(inp, start=pos, stop=end, dtype='float32', always_2d=True)
            y = np.asarray(process(x.mean(1), sr), dtype=np.float32).reshape(-1)
            if tail is not None:
                n = min(ov_o, len(y))
                y[:n] = tail[:n] * fade_out[:n] + y[:n] * fade_in[:n]
            log(f"seg {i} {pos / sr:.0f}-{end / sr:.0f}s")
            if end >= s1:
                fo.write(np.clip(y, -1, 1))
                break
            fo.write(np.clip(y[:len(y) - ov_o], -1, 1))
            tail = y[len(y) - ov_o:]
            pos += seg_n - ov_n
            i += 1
