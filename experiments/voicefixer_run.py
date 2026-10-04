"""VoiceFixer segmented restoration. Output 44.1 kHz mono 16-bit (VoiceFixer native 44.1k vocoder).
usage: python voicefixer_run.py in.wav out.wav [--mode 0|1|2] [--seg 30] [--ov 1] [--cpu]
env: envs/vf"""
import argparse, time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import numpy as np, torch, torchaudio, soundfile as sf
from segproc import run_segmented
from voicefixer import VoiceFixer
ap = argparse.ArgumentParser()
ap.add_argument('inp'); ap.add_argument('out')
ap.add_argument('--mode', type=int, default=0)
ap.add_argument('--seg', type=float, default=30); ap.add_argument('--ov', type=float, default=1.0)
ap.add_argument('--start', type=float, default=0); ap.add_argument('--dur', type=float, default=None)
ap.add_argument('--cpu', action='store_true')
a = ap.parse_args()
vf = VoiceFixer()
cuda = torch.cuda.is_available() and not a.cpu
def proc(x, sr):
    n = len(x)
    w = torch.from_numpy(x)[None]
    w44 = torchaudio.functional.resample(w, sr, 44100) if sr != 44100 else w
    with torch.no_grad():
        y = vf.restore_inmem(w44.numpy()[0], cuda=cuda, mode=a.mode)  # expects 1-D np array
    y = np.asarray(y).reshape(-1)
    n_out = int(round(n*44100/sr))
    if len(y) < n_out: y = np.pad(y, (0, n_out-len(y)))
    return y[:n_out].astype(np.float32)
t0 = time.time()
run_segmented(a.inp, a.out, proc, 44100, a.seg, a.ov, start=a.start, dur=a.dur)
n = sf.info(a.out).duration
print(f"done {n:.1f}s audio in {time.time()-t0:.1f}s -> {(time.time()-t0)/n*60:.2f} compute-s/min")
