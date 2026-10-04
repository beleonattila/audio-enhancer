#!/usr/bin/env python
"""
ClearerVoice-Studio runner: segmented processing with crossfade, chainable stages.

Usage (use the envs/clearvoice python):
  python clearvoice_run.py INPUT OUTPUT CHAIN [--seg 60] [--overlap 1.0] [--out-sr 48000]
                [--save-intermediate] [--start S --dur D]

CHAIN is a comma-separated list of stages, run in order:
  se48   MossFormer2_SE_48K  (speech enhancement, 48 kHz)
  sr48   MossFormer2_SR_48K  (speech super-resolution -> 48 kHz)
  frcrn  FRCRN_SE_16K        (speech enhancement, 16 kHz native)
  gan16  MossFormerGAN_SE_16K
Examples:
  python clearvoice_run.py in.wav out.wav se48
  python clearvoice_run.py in.wav out.wav sr48
  python clearvoice_run.py in.wav out.wav se48,sr48
  python clearvoice_run.py in.mp3 out.wav se48,sr48 --seg 90

Input may be any format soundfile/ffmpeg can read (mono mix is taken for stereo).
Output is 16-bit PCM wav at the final stage's native rate (or --out-sr).
Checkpoints are downloaded from HuggingFace on first use into models/clearvoice/checkpoints/.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

ORIG_CWD = os.getcwd()
HERE = Path(__file__).resolve().parents[1] / "models" / "clearvoice"
HERE.mkdir(parents=True, exist_ok=True)
os.chdir(HERE)  # clearvoice downloads checkpoints relative to cwd

def ffmpeg_exe():
    """$FFMPEG, else ffmpeg on PATH, else the imageio-ffmpeg binary (only needed for non-wav input)."""
    import shutil
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    return exe

DEFAULT_SEG = {"se48": 60.0, "sr48": 10.0, "frcrn": 60.0, "gan16": 60.0}  # SR is superlinear in length -> short segs
PAD_FIXED = {"sr48"}
STAGES = {
    "se48": ("speech_enhancement", "MossFormer2_SE_48K", 48000),
    "sr48": ("speech_super_resolution", "MossFormer2_SR_48K", 48000),
    "frcrn": ("speech_enhancement", "FRCRN_SE_16K", 16000),
    "gan16": ("speech_enhancement", "MossFormerGAN_SE_16K", 16000),
}


def resample(x, sr_from, sr_to):
    if sr_from == sr_to:
        return x
    import soxr
    return soxr.resample(x, sr_from, sr_to, quality="VHQ").astype(np.float32)


def load_audio(path):
    try:
        x, sr = sf.read(path, dtype="float32", always_2d=True)
    except Exception:
        tmp = HERE / "_tmp_in.wav"
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", path, "-c:a", "pcm_f32le", str(tmp)], check=True)
        x, sr = sf.read(str(tmp), dtype="float32", always_2d=True)
        tmp.unlink()
    return x.mean(axis=1).astype(np.float32), sr


_model_cache = {}
STAGE_TIME = []  # pure inference seconds per stage (excludes model load)


def get_model(key):
    if key in _model_cache:
        return _model_cache[key]
    from clearvoice import ClearVoice
    task, name, _ = STAGES[key]
    cv = ClearVoice(task=task, model_names=[name])
    m = cv.models[0]
    m.args.one_time_decode_length = 1e6  # we do our own segmenting; never trigger clearvoice's (buggy) online path
    _model_cache[key] = m
    return m


def run_segment(m, key, seg):
    """seg: 1-D float32 at the stage's native rate -> 1-D float32 same length."""
    n = len(seg)
    with torch.no_grad():
        out = m.decode_data(seg[None, :].astype(np.float32))
    out = np.asarray(out)
    out = out.reshape(-1)  # [1,T] -> [T]
    if len(out) < n:
        out = np.pad(out, (0, n - len(out)))
    return out[:n].astype(np.float32)


def run_stage(key, x, sr_in, seg_s, ov_s):
    """Run one stage over full signal x (sr_in). Returns (y, sr_out)."""
    _, _, sr = STAGES[key]
    x = resample(x, sr_in, sr)
    m = get_model(key)
    scalar = 1.0
    if key in ("frcrn",):  # clearvoice recommends global loudness norm for these
        from clearvoice.dataloader.dataloader import audio_norm
        x, scalar = audio_norm(x.astype(np.float64))
        x = x.astype(np.float32)
    N = len(x)
    seg = int(seg_s * sr)
    ov = int(ov_s * sr)
    y = np.zeros(N, dtype=np.float32)
    pos = 0
    first = True
    t0 = time.time()
    while pos < N:
        end = min(N, pos + seg + ov)
        chunk = x[pos:end]
        if key in PAD_FIXED and len(chunk) < seg + ov:  # fixed input shape: avoids pathological cuDNN slowdowns on odd lengths
            chunk = np.pad(chunk, (0, seg + ov - len(chunk)))
        # retry on OOM
        for attempt in range(6):
            try:
                o = run_segment(m, key, chunk)[:end - pos]
                break
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                print(f"  OOM at {pos/sr:.0f}s, waiting and retrying ({attempt+1})", flush=True)
                time.sleep(15)
        else:
            raise RuntimeError("repeated CUDA OOM; reduce --seg")
        if first:
            y[pos:end] = o
            first = False
        else:
            # crossfade the first `ov` samples with what is already in y
            k = min(ov, len(o))
            w = np.linspace(0.0, 1.0, k, dtype=np.float32)
            y[pos:pos + k] = y[pos:pos + k] * (1 - w) + o[:k] * w
            y[pos + k:end] = o[k:]
        el = time.time() - t0
        print(f"  [{key}] {end/sr:7.1f}/{N/sr:.1f}s  elapsed {el:.1f}s", flush=True)
        if end >= N:
            break
        pos += seg
    STAGE_TIME.append(time.time() - t0)
    y = y * scalar
    return y, sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("chain", help="comma list of: " + ",".join(STAGES))
    ap.add_argument("--seg", type=float, default=None, help="segment length in s (excluding overlap); default per stage: " + str(DEFAULT_SEG))
    ap.add_argument("--overlap", type=float, default=1.0, help="overlap/crossfade in s")
    ap.add_argument("--out-sr", type=int, default=None)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=None)
    ap.add_argument("--save-intermediate", action="store_true")
    a = ap.parse_args()
    a.input = os.path.abspath(os.path.join(ORIG_CWD, a.input))
    a.output = os.path.abspath(os.path.join(ORIG_CWD, a.output))

    chain = [c.strip() for c in a.chain.split(",") if c.strip()]
    for c in chain:
        if c not in STAGES:
            sys.exit(f"unknown stage {c}")

    x, sr = load_audio(a.input)
    if a.start or a.dur:
        s = int(a.start * sr)
        x = x[s: s + int(a.dur * sr)] if a.dur else x[s:]
    in_dur = len(x) / sr
    print(f"input {a.input}: {in_dur:.1f}s @ {sr} Hz, chain={chain}", flush=True)

    t0 = time.time()
    for i, key in enumerate(chain):
        x, sr = run_stage(key, x, sr, a.seg or DEFAULT_SEG[key], a.overlap)
        if a.save_intermediate and i < len(chain) - 1:
            p = Path(a.output).with_suffix(f".{i}_{key}.wav")
            sf.write(str(p), x, sr, subtype="PCM_16")
        torch.cuda.empty_cache()
    if a.out_sr:
        x = resample(x, sr, a.out_sr)
        sr = a.out_sr
    el = time.time() - t0

    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.abs(x).max())
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    if peak > 1.0:
        print(f"WARNING peak {peak:.3f} > 1.0 -> scaling to 0.98 to avoid clipping")
        x = x * (0.98 / peak)
    sf.write(a.output, x, sr, subtype="PCM_16")
    print(f"wrote {a.output}: {len(x)/sr:.1f}s @ {sr} Hz  rms={20*np.log10(rms+1e-12):.1f} dBFS peak={20*np.log10(peak+1e-12):.1f} dBFS")
    inf = sum(STAGE_TIME)
    print(f"inference {inf:.1f}s ({inf/(in_dur/60):.1f} s per audio-minute); total incl. model load {el:.1f}s")


if __name__ == "__main__":
    main()
