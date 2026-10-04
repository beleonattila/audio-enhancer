"""DeepFilterNet3 noise suppression, segmented. Output: 48 kHz mono.

usage: python dfn_enhance.py in.wav out.wav [--atten 15] [--seg 90] [--ov 1] [--start S --dur D]
env:   envs/dfn
"""
import argparse, os, sys, time, warnings
sys.path.insert(0, os.path.dirname(__file__))
warnings.filterwarnings('ignore')
import soundfile as sf, torch, torchaudio
from segproc import run_segmented
from df.enhance import init_df, enhance

ap = argparse.ArgumentParser()
ap.add_argument('inp'); ap.add_argument('out')
ap.add_argument('--atten', type=float, default=None, help='attenuation limit in dB (default: unlimited)')
ap.add_argument('--seg', type=float, default=90); ap.add_argument('--ov', type=float, default=1.0)
ap.add_argument('--start', type=float, default=0); ap.add_argument('--dur', type=float, default=None)
a = ap.parse_args()
model, st, _ = init_df(log_level='ERROR', log_file=None)


def proc(x, sr):
    t = torchaudio.functional.resample(torch.from_numpy(x)[None], sr, 48000)
    return enhance(model, st, t, atten_lim_db=a.atten)[0].cpu().numpy()


t0 = time.time()
run_segmented(a.inp, a.out, proc, 48000, a.seg, a.ov, start=a.start, dur=a.dur)
n = sf.info(a.out).duration
print(f"done {n:.1f}s audio in {time.time() - t0:.1f}s -> {(time.time() - t0) / n * 60:.2f} compute-s/min")
