#!/usr/bin/env python3
"""Animated HRTF sequences from real IRCAM SOFA data (frames -> dirs)."""
import glob, os, numpy as np, h5py
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import correlate
from scipy.interpolate import interp1d

BG="#0a0e14"; PANEL="#121b24"; CYAN="#35e0d0"; AMBER="#f5b556"; INK="#e8eef0"; MUTED="#8a9aa3"; GRID="#1e2a33"
plt.rcParams.update({"figure.facecolor":BG,"axes.facecolor":BG,"savefig.facecolor":BG,
 "text.color":INK,"axes.labelcolor":INK,"xtick.color":MUTED,"ytick.color":MUTED,
 "axes.edgecolor":GRID,"grid.color":GRID,"font.family":"DejaVu Sans Mono","font.size":16})
OUT="/tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut"
SOFA=sorted(glob.glob('/home/jean/git/binaural-localization/data/ircam_sofa/IRC_*_R_44100.sofa'))
SR=44100.0
def load(f):
    d=h5py.File(f,'r'); ir=np.array(d['Data.IR']); sp=np.array(d['SourcePosition']); d.close(); return ir,sp
def horiz(sp):
    idx=np.where(np.abs(sp[:,1])<=1.0)[0]; az=sp[idx,0].copy(); az[az>180]-=360
    o=np.argsort(az); return idx[o], az[o]
def magdb(hrir,n=512,nfft=2048):
    w=np.hanning(n); H=np.fft.rfft(hrir[:n]*w,nfft); return 20*np.log10(np.abs(H)+1e-6)
FREQ=np.fft.rfftfreq(2048,1/SR)
def itd_curve(ir,idx,az):
    out=[]
    for m in idx:
        L=ir[m,0,:1024].astype(float); R=ir[m,1,:1024].astype(float)
        c=np.abs(correlate(L,R,mode='full')); p=int(np.argmax(c))
        if 0<p<len(c)-1:
            den=(c[p-1]-2*c[p]+c[p+1]); d=0.5*(c[p-1]-c[p+1])/den if den!=0 else 0
        else: d=0
        out.append(((p-(len(L)-1))+d)/SR*1e3)
    return np.array(out)

def savef(fig,d,i): fig.savefig(os.path.join(d,f"{i:05d}.png"));
def mkd(n): p=os.path.join(OUT,n); os.makedirs(p,exist_ok=True); return p

ir0,sp0=load(SOFA[0]); idx0,az0=horiz(sp0); itd0=itd_curve(ir0,idx0,az0)

# ---- clip A: ITD curve draws on ----
d=mkd("fa_itd"); fig,ax=plt.subplots(figsize=(19.2,10.8),dpi=100)
N=170
for i in range(N):
    p=min(1.0,i/120); k=max(2,int(2+p*(len(az0)-2)))
    ax.cla()
    ax.axhline(0,color=GRID,lw=1)
    ax.plot(az0[:k],itd0[:k],color=CYAN,lw=4)
    ax.fill_between(az0[:k],itd0[:k],0,color=CYAN,alpha=0.12)
    ax.set_xlim(-180,180); ax.set_ylim(-1.1,1.1); ax.set_xticks([-180,-90,0,90,180]); ax.grid(alpha=0.5)
    ax.set_xlabel("sound direction   (deg,  0=front,  +=right)",fontsize=20)
    ax.set_ylabel("interaural time difference  (ms)",fontsize=20)
    ax.set_title("Measured on a real head: the nearer ear always hears it first",color=INK,fontsize=26,pad=18)
    savef(fig,d,i)
plt.close(fig); print("fa_itd done")

# ---- clip B: spectrum morphs as the sound direction sweeps ----
M=np.stack([magdb(ir0[m,1]) for m in idx0],axis=0)         # (naz, nfreq) right ear
fI=interp1d(az0,M,axis=0,bounds_error=False,fill_value=(M[0],M[-1]))
d=mkd("fb_spec"); fig=plt.figure(figsize=(19.2,10.8),dpi=100)
ax=fig.add_axes([0.08,0.12,0.88,0.72]); ins=fig.add_axes([0.70,0.60,0.22,0.30]); ins.set_aspect('equal')
Nb=300
for i in range(Nb):
    a=150*np.sin(2*np.pi*i/Nb)                             # sweep right<->left
    ax.cla()
    ax.semilogx(FREQ,fI(a),color=CYAN,lw=3)
    ax.fill_between(FREQ,fI(a),-40,color=CYAN,alpha=0.08)
    ax.set_xlim(200,20000); ax.set_ylim(-35,20); ax.grid(alpha=0.4,which='both')
    ax.set_xlabel("frequency  (Hz)",fontsize=20); ax.set_ylabel("right-ear gain  (dB)",fontsize=20)
    ax.set_title("Same sound, different direction = a different tonal fingerprint (HRTF)",color=INK,fontsize=26,pad=18)
    side = "front" if abs(a)<8 else (f"{abs(a):.0f}° right" if a>0 else f"{abs(a):.0f}° left")
    ax.text(230,16,f"sound at: {side}",color=AMBER,fontsize=20,va='top')
    # inset head + source dot
    ins.cla(); ins.axis('off'); ins.set_xlim(-1.4,1.4); ins.set_ylim(-1.4,1.4)
    th=np.linspace(0,2*np.pi,60); ins.plot(np.cos(th),np.sin(th),color=CYAN,lw=2)
    ins.plot([0,0],[1,1.25],color=CYAN,lw=2)               # nose/front
    ar=np.radians(a); ins.plot([np.sin(ar)*1.15],[np.cos(ar)*1.15],'o',color=AMBER,ms=14)
    savef(fig,d,i)
plt.close(fig); print("fb_spec done")

# ---- clip C: individuality, many real people draw on ----
curves=[]
for f_ in SOFA[:30]:
    try:
        ir2,sp2=load(f_); idx2,az2=horiz(sp2); j=idx2[np.argmin(np.abs(az2-45))]
        curves.append(magdb(ir2[j,1]))
    except Exception: pass
d=mkd("fc_indiv"); fig,ax=plt.subplots(figsize=(19.2,10.8),dpi=100)
Nc=240; nppl=len(curves)
for i in range(Nc):
    show=min(nppl,int(np.ceil((i/170)*nppl))) if i<170 else nppl
    ax.cla()
    for c in curves[:show]:
        ax.semilogx(FREQ,c,color=CYAN,lw=1.3,alpha=0.5)
    ax.set_xlim(200,20000); ax.set_ylim(-40,22); ax.grid(alpha=0.4,which='both')
    ax.set_xlabel("frequency  (Hz)",fontsize=20); ax.set_ylabel("right-ear gain  (dB)",fontsize=20)
    ax.set_title(f"Same direction, {show} different real people - every pair of ears is unique",color=INK,fontsize=26,pad=18)
    ax.text(230,18,"each line = one person's HRTF\nIRCAM LISTEN database",color=MUTED,fontsize=16,va='top')
    if show>=nppl:
        ax.axvspan(6000,20000,color=AMBER,alpha=0.06)
        ax.text(9000,-34,"pinna region:\nwhere we differ most",color=AMBER,fontsize=16,va='bottom')
    savef(fig,d,i)
plt.close(fig); print("fc_indiv done",nppl,"subjects")
print("ALL FRAMES DONE")
