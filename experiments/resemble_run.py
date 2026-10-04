"""resemble-enhance runner (Windows, no deepspeed needed).

python resemble_run.py IN.wav OUT.wav --mode {denoise,enhance} [--lambd 0.5 --tau 0.5 --nfe 64 --solver midpoint]
                              [--segment 300 --overlap 2]
Long files are processed in ~segment-second pieces with `overlap` seconds crossfade.
Output is 44.1 kHz mono float WAV (PCM_24 by default; --subtype to change).
"""
import argparse, sys, time, types
from pathlib import Path

# deepspeed is only used for training; stub it so resemble_enhance imports.
for name in ("deepspeed", "deepspeed.accelerator", "deepspeed.runtime", "deepspeed.runtime.engine", "deepspeed.runtime.utils"):
    m = types.ModuleType(name)
    m.DeepSpeedConfig = object
    m.DeepSpeedEngine = object
    m.get_accelerator = lambda: None
    m.clip_grad_norm_ = None
    sys.modules[name] = m

import pathlib
pathlib.PosixPath = pathlib.WindowsPath  # hparams.yaml was saved on Linux and contains PosixPath tags

import numpy as np
import soundfile as sf
import torch

MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "resemble" / "enhancer_stage2"


def ensure_model():
    # the package's own downloader needs git-lfs; fetch the stage-2 enhancer weights via huggingface_hub instead
    if not (MODEL_DIR / "hparams.yaml").exists():
        from huggingface_hub import snapshot_download
        snapshot_download("ResembleAI/resemble-enhance", allow_patterns=["enhancer_stage2/*"], local_dir=MODEL_DIR.parent)


def process(dwav, sr, args, device):
    from resemble_enhance.enhancer.inference import load_enhancer
    from resemble_enhance.inference import inference
    ensure_model()
    enhancer = load_enhancer(MODEL_DIR, device)
    with torch.inference_mode():
        if args.mode == "denoise":
            model = enhancer.denoiser
        else:
            enhancer.configurate_(nfe=args.nfe, solver=args.solver, lambd=args.lambd, tau=args.tau)
            model = enhancer
        out, osr = inference(model=model, dwav=dwav, sr=sr, device=device,
                             chunk_seconds=args.chunk, overlap_seconds=args.chunk_overlap)
    return out.float().cpu(), osr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out")
    ap.add_argument("--mode", choices=["denoise", "enhance"], default="enhance")
    ap.add_argument("--lambd", type=float, default=0.5)
    ap.add_argument("--tau", type=float, default=0.5)
    ap.add_argument("--nfe", type=int, default=64)
    ap.add_argument("--solver", default="midpoint")
    ap.add_argument("--segment", type=float, default=300.0, help="segment length in s")
    ap.add_argument("--overlap", type=float, default=2.0, help="crossfade overlap in s")
    ap.add_argument("--chunk", type=float, default=30.0, help="internal model chunk in s (lower = less VRAM)")
    ap.add_argument("--chunk-overlap", type=float, default=1.0)
    ap.add_argument("--subtype", default="PCM_24")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    data, sr = sf.read(args.inp, dtype="float32", always_2d=True)
    dwav = torch.from_numpy(data.mean(axis=1))
    dur = len(dwav) / sr
    print(f"in: {args.inp} sr={sr} dur={dur:.1f}s mode={args.mode} lambd={args.lambd} tau={args.tau} nfe={args.nfe}", flush=True)

    seg, ov = int(args.segment * sr), int(args.overlap * sr)
    hop = seg - ov
    if hop <= 0:
        sys.exit("segment must exceed overlap")

    if args.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    if len(dwav) <= seg + ov:
        out, osr = process(dwav, sr, args, args.device)
    else:
        starts = list(range(0, len(dwav) - ov, hop))
        pieces = []
        osr = None
        for i, s in enumerate(starts):
            part = dwav[s:s + seg]
            print(f"segment {i+1}/{len(starts)} [{s/sr:.0f}s..{(s+len(part))/sr:.0f}s]", flush=True)
            o, osr = process(part, sr, args, args.device)
            pieces.append(o)
            if args.device == "cuda":
                torch.cuda.empty_cache()
        ratio = osr / sr
        ohop, oov = int(round(hop * ratio)), int(round(ov * ratio))
        total = int(round(len(dwav) * ratio))
        out = torch.zeros(total)
        wsum = torch.zeros(total)
        for i, p in enumerate(pieces):
            st = i * ohop
            n = min(len(p), total - st)
            w = torch.ones(n)
            if i > 0:  # fade in over overlap (equal-power-ish via sin^2 / cos^2 pair sums to 1)
                k = min(oov, n)
                w[:k] = torch.sin(torch.linspace(0, np.pi / 2, k)) ** 2
            if i < len(pieces) - 1:
                k = min(oov, n)
                w[n - k:] = w[n - k:] * torch.cos(torch.linspace(0, np.pi / 2, k)) ** 2
            out[st:st + n] += p[:n] * w
            wsum[st:st + n] += w
        out = out / wsum.clamp(min=1e-6)
    elapsed = time.perf_counter() - t0

    y = out.numpy()
    peak = float(np.abs(y).max()); rms = float(np.sqrt((y ** 2).mean()))
    if peak > 0.99:
        y = y * (0.99 / peak)
        print(f"peak {peak:.3f} > 0.99, scaled down", flush=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.out, y, osr, subtype=args.subtype)
    vram = torch.cuda.max_memory_allocated() / 2**20 if args.device == "cuda" else 0
    print(f"out: {args.out} sr={osr} dur={len(y)/osr:.1f}s peak={peak:.3f} rms={rms:.4f} ({20*np.log10(rms+1e-9):.1f} dBFS) "
          f"compute={elapsed:.1f}s ({elapsed/(dur/60):.1f}s/min) peakVRAM={vram:.0f}MiB", flush=True)


if __name__ == "__main__":
    main()
