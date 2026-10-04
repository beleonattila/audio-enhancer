"""Sidon speech restoration (sarulab-speech/sidon-v0.1, TorchScript), segmented. Output: 48 kHz mono.

usage: python sidon_enhance.py in.wav out.wav [--seg 30] [--ov 1] [--start S --dur D] [--weights DIR]
env:   envs/sidon (torch 2.5.1+cu124, transformers). Needs a CUDA GPU (the released weights are CUDA TorchScript).
Weights are downloaded from Hugging Face into models/sidon on first run.
The input gets ONE global gain (0.9 / file peak) so every segment sees the same level.
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np, soundfile as sf, torch, torchaudio, transformers
from segproc import run_segmented

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument('inp'); ap.add_argument('out')
ap.add_argument('--seg', type=float, default=30, help='segment length in s (30 s ≈ 2.2 GB VRAM)')
ap.add_argument('--ov', type=float, default=1.0)
ap.add_argument('--start', type=float, default=0); ap.add_argument('--dur', type=float, default=None)
ap.add_argument('--weights', default=os.path.join(ROOT, 'models', 'sidon'))
a = ap.parse_args()

files = ('feature_extractor_cuda.pt', 'decoder_cuda.pt')
if not all(os.path.exists(os.path.join(a.weights, f)) for f in files):
    from huggingface_hub import hf_hub_download
    for f in files:
        hf_hub_download('sarulab-speech/sidon-v0.1', f, local_dir=a.weights)

dev = 'cuda'
fe = torch.jit.load(os.path.join(a.weights, files[0]), map_location=dev).to(dev).eval()
dec = torch.jit.load(os.path.join(a.weights, files[1]), map_location=dev).to(dev).eval()
pre = transformers.SeamlessM4TFeatureExtractor.from_pretrained('facebook/w2v-bert-2.0')

pk = 0.0
with sf.SoundFile(a.inp) as f:
    for blk in f.blocks(blocksize=f.samplerate * 30, dtype='float32', always_2d=True):
        pk = max(pk, float(np.abs(blk).max()))
gain = 0.9 / max(pk, 1e-6)


@torch.inference_mode()
def proc(x, sr):
    n_out = int(round(48000 / sr * len(x)))
    w = torch.from_numpy(x * gain)[None]
    w = torchaudio.functional.highpass_biquad(w, sr, 50)
    w16 = torchaudio.functional.resample(w, sr, 16000)
    w16 = torch.nn.functional.pad(w16, (0, 24000))
    inp = pre(torch.nn.functional.pad(w16.view(-1), (160, 160)), sampling_rate=16000, return_tensors='pt')
    feat = fe(inp['input_features'].to(dev))['last_hidden_state']
    y = dec(feat.transpose(1, 2)).view(-1)[:-960]
    y = y.float().cpu().numpy()[:n_out]
    return np.pad(y, (0, max(0, n_out - len(y))))


t0 = time.time()
run_segmented(a.inp, a.out, proc, 48000, a.seg, a.ov, start=a.start, dur=a.dur)
n = sf.info(a.out).duration
print(f"done {n:.1f}s audio in {time.time() - t0:.1f}s -> {(time.time() - t0) / n * 60:.2f} compute-s/min; "
      f"peak VRAM {torch.cuda.max_memory_allocated() / 1e9:.2f} GB")
