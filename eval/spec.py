"""Stacked spectrograms of the first 20 s of each file. Usage: spec.py out.png a.wav b.wav ..."""
import sys, numpy as np, soundfile as sf, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
files=sys.argv[2:]; out=sys.argv[1]
fig,axs=plt.subplots(len(files),1,figsize=(16,3.2*len(files)),squeeze=False)
for ax,f in zip(axs[:,0],files):
    x,sr=sf.read(f,dtype='float32'); x=x.mean(1) if x.ndim>1 else x
    x=x[:sr*20]
    ax.specgram(x,NFFT=2048,Fs=sr,noverlap=1536,cmap='magma',vmin=-140,vmax=-30); ax.set_ylim(0,min(sr/2,22050)); ax.set_title(f.split('/')[-1])
plt.tight_layout(); plt.savefig(out,dpi=60)
