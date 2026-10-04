"""Level / stereo / loudness / octave-band and noise-floor report for one file (first 10 min). Usage: stats.py file.wav"""
import sys, numpy as np, soundfile as sf, pyloudnorm as pyln
x, sr = sf.read(sys.argv[1], dtype='float32')
if x.ndim==1: x=x[:,None]
print("sr",sr,"shape",x.shape,"dur min",x.shape[0]/sr/60)
if x.shape[1]==2:
    c=np.corrcoef(x[::10,0],x[::10,1])[0,1]; print("L/R corr",c, "L rms",np.sqrt((x[:,0]**2).mean()),"R rms",np.sqrt((x[:,1]**2).mean()))
m=x.mean(1)
print("LUFS",pyln.Meter(sr).integrated_loudness(m), "peak dBFS",20*np.log10(np.abs(x).max()+1e-12))
# frame rms distribution
f=int(0.05*sr); n=len(m)//f; r=np.sqrt((m[:n*f].reshape(n,f)**2).mean(1)+1e-12); db=20*np.log10(r)
print("frame dB pct 5/10/50/90/99:",np.percentile(db,[5,10,50,90,99]).round(1))
# spectrum of loud vs quiet frames
import scipy.signal as ss
seg=m[:sr*600]
fr,P=ss.welch(seg,sr,nperseg=4096)
for lo,hi in [(0,80),(80,200),(200,500),(500,1000),(1000,2000),(2000,4000),(4000,8000),(8000,11000),(11000,14000),(14000,16000),(16000,18000),(18000,22050)]:
    s=(fr>=lo)&(fr<hi); print(f"{lo}-{hi} Hz: {10*np.log10(P[s].mean()+1e-20):.1f} dB")
# noise floor spectrum: quietest 5% frames
fr2=int(0.05*sr); idx=np.argsort(db[:12000])[:200]
q=np.concatenate([m[i*fr2:(i+1)*fr2] for i in idx])
fr,Pq=ss.welch(q,sr,nperseg=2048)
print("noise floor spectrum (quiet frames):")
for lo,hi in [(0,80),(80,200),(200,500),(500,1000),(1000,2000),(2000,4000),(4000,8000),(8000,16000)]:
    s=(fr>=lo)&(fr<hi); print(f"  {lo}-{hi}: {10*np.log10(Pq[s].mean()+1e-20):.1f}")
pk=fr[np.argsort(Pq)[-8:]]; print("  peak noise freqs",np.sort(pk).round(0))
