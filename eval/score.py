"""Score wavs with DNSMOS P.835 (SIG/BAK/OVRL) and UTMOS22. Usage: score.py file1.wav file2.wav ...  (run with the .venv python)
bw95% = frequency below which 99.5% of the spectral energy above 1 kHz lies (rough effective bandwidth)."""
import os, sys, numpy as np, soundfile as sf, torch, librosa, warnings; warnings.filterwarnings("ignore")
sys.stdout.reconfigure(line_buffering=True)
from torchmetrics.functional.audio.dnsmos import deep_noise_suppression_mean_opinion_score as dnsmos
utm = torch.hub.load("tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True).eval()
print(f"{'file':42s} {'SIG':>5} {'BAK':>5} {'OVRL':>5} {'P808':>5} {'UTMOS':>5} {'bw95%':>6}")
for f in sys.argv[1:]:
    x, sr = sf.read(f, dtype='float32'); x = x.mean(1) if x.ndim > 1 else x
    x16 = librosa.resample(x, orig_sr=sr, target_sr=16000)
    d = dnsmos(torch.from_numpy(x16), 16000, personalized=False).numpy()  # [p808, sig, bak, ovrl]
    # UTMOS on 10 s windows, averaged
    seg = 160000; us = []
    with torch.no_grad():
        for i in range(0, len(x16) - seg + 1, seg):
            us.append(utm(torch.from_numpy(x16[i:i+seg])[None], 16000).item())
    # effective bandwidth: freq below which 99.5% of spectral energy above 1k lies
    S = np.abs(librosa.stft(x, n_fft=4096))**2; p = S.mean(1); fr = librosa.fft_frequencies(sr=sr, n_fft=4096)
    m = fr > 1000; c = np.cumsum(p[m]) / p[m].sum(); bw = fr[m][np.searchsorted(c, 0.995)]
    print(f"{os.path.basename(f)[:42]:42s} {d[1]:5.2f} {d[2]:5.2f} {d[3]:5.2f} {d[0]:5.2f} {np.mean(us):5.2f} {bw:6.0f}")
