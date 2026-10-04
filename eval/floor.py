"""Pause noise floor: 50 ms frame levels at the 3rd/10th/50th percentile, relative to the 95th (speech) percentile.
Usage: floor.py a.wav b.wav ..."""
import sys, numpy as np, soundfile as sf
for f in sys.argv[1:]:
    x,sr=sf.read(f,dtype='float32'); x=x.mean(1) if x.ndim>1 else x
    n=int(.05*sr); k=len(x)//n; db=10*np.log10((x[:k*n].reshape(k,n)**2).mean(1)+1e-14)
    p=np.percentile(db,[3,10,50,95]); print(f"{f.split('/')[-1]:34s} p3 {p[0]-p[3]:6.1f}  p10 {p[1]-p[3]:6.1f}  p50 {p[2]-p[3]:6.1f} (dB rel. p95)")
