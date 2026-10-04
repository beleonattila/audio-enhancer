"""Find spans where a restored file lost speech that a reference (lightly denoised original) still has.
Usage: dropouts.py reference.wav restored.wav [--deficit 12] [--min-ms 100]
Speech-band (150-4000 Hz) energy per 20 ms frame, each file normalised to its own active-speech level."""
import argparse, numpy as np, soundfile as sf, scipy.signal as ss


def band_env(path, hop=0.02):
    x, sr = sf.read(path, dtype='float32', always_2d=True); x = x.mean(1)
    b, a = ss.butter(4, [150, 4000], 'bandpass', fs=sr); x = ss.filtfilt(b, a, x)
    n = int(hop * sr); k = len(x) // n
    e = 10 * np.log10((x[:k * n].reshape(k, n) ** 2).mean(1) + 1e-12)
    e = np.convolve(e, np.ones(3) / 3, 'same')                      # 60 ms smoothing
    return e - np.percentile(e, 90)                                 # 0 dB ≈ loud speech


def find(ref, out, deficit=12.0, min_ms=100, presence=-28.0, hop=0.02):
    r, o = band_env(ref, hop), band_env(out, hop); n = min(len(r), len(o)); r, o = r[:n], o[:n]
    bad = (r > presence) & (o - r < -deficit)
    spans, i = [], 0
    while i < n:
        if bad[i]:
            j = i
            while j < n and bad[j]: j += 1
            if (j - i) * hop * 1000 >= min_ms:
                spans.append((i * hop, j * hop, float(np.median(o[i:j] - r[i:j]))))
            i = j
        else:
            i += 1
    return spans


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('ref'); ap.add_argument('out')
    ap.add_argument('--deficit', type=float, default=12); ap.add_argument('--min-ms', type=float, default=100)
    a = ap.parse_args()
    sp = find(a.ref, a.out, a.deficit, a.min_ms)
    for s, e, d in sp: print(f"{int(s // 60)}:{s % 60:05.2f} - {int(e // 60)}:{e % 60:05.2f}  ({(e - s) * 1000:.0f} ms, {d:.0f} dB)")
    print(f"{len(sp)} dropouts, {sum(e - s for s, e, _ in sp):.1f} s total")
