#!/usr/bin/env python3
"""Animated HRTF sequences from real IRCAM SOFA data (frames -> dirs).
v2: house chrome, de-jargoned axes, median+band individuality, sequential colours."""
import glob, os, numpy as np, h5py
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d
import vizchrome as vc
vc.style()
CYAN,AMBER,INK,MUTED,GRID = vc.CYAN,vc.AMBER,vc.INK,vc.MUTED,vc.GRID
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
def mkd(n): p=os.path.join(OUT,n); os.makedirs(p,exist_ok=True); return p
def octave_smooth(m, frac=12):
    # light 1/frac-octave smoothing for display (kills the HF hash)
    out=np.empty_like(m)
    for i,f in enumerate(FREQ):
        if f<=0: out[i]=m[i]; continue
        lo=np.searchsorted(FREQ,f/2**(1/(2*frac))); hi=np.searchsorted(FREQ,f*2**(1/(2*frac)))
        out[i]=np.mean(m[lo:hi+1]) if hi>=lo else m[i]
    return out

ir0,sp0=load(SOFA[0]); idx0,az0=horiz(sp0)

# ===== clip 3: spectrum morphs as the sound direction sweeps =====
M=np.stack([octave_smooth(magdb(ir0[m,1])) for m in idx0],axis=0)
fI=interp1d(az0,M,axis=0,bounds_error=False,fill_value=(M[0],M[-1]))
d=mkd("fb_spec")
fig,ax=vc.new_fig(); ins=fig.add_axes([0.70,0.52,0.20,0.26]); ins.set_aspect('equal')
Nb=300
for i in range(Nb):
    a=150*np.sin(2*np.pi*i/Nb)
    ax.cla(); ax.semilogx(FREQ,fI(a),color=CYAN,lw=3); ax.fill_between(FREQ,fI(a),-40,color=CYAN,alpha=0.08)
    ax.axhline(0,color=MUTED,lw=1.2,ls='--'); ax.text(210,1.5,"unchanged",color=MUTED,fontsize=14,va='bottom')
    ax.set_xlim(200,20000); ax.set_ylim(-40,8); vc.khz(ax); ax.grid(alpha=0.35,which='major')
    ax.set_xlabel("pitch  (frequency)",fontsize=20,color=INK)
    ax.set_ylabel("tone colour  (dB,  + brighter / - duller)",fontsize=20,color=INK)
    side = "front" if abs(a)<8 else (f"{abs(a):.0f} deg right" if a>0 else f"{abs(a):.0f} deg left")
    ax.text(210,6.5,f"sound at: {side}",color=AMBER,fontsize=22,va='top')
    ins.cla(); ins.axis('off'); ins.set_xlim(-1.4,1.4); ins.set_ylim(-1.4,1.4)
    th=np.linspace(0,2*np.pi,60); ins.plot(np.cos(th),np.sin(th),color=CYAN,lw=2); ins.plot([0,0],[1,1.25],color=CYAN,lw=2)
    ar=np.radians(a); ins.plot([np.sin(ar)*1.15],[np.cos(ar)*1.15],'o',color=AMBER,ms=14)
    vc.chrome(fig,"04b","COLOUR","Same sound, a new direction = a new tone colour")
    fig.savefig(os.path.join(d,f"{i:05d}.png")); [a2.remove() for a2 in fig.axes if a2 not in (ax,ins)]
plt.close(fig); print("fb_spec done")

# ===== clip 4: individuality -> many real people, drawn one by one =====
curves=[]
for f_ in SOFA[:30]:
    try:
        ir2,sp2=load(f_); idx2,az2=horiz(sp2); j=idx2[np.argmin(np.abs(az2-45))]
        curves.append(octave_smooth(magdb(ir2[j,1]),frac=6))   # light smoothing keeps the pinna notches
    except Exception: pass
nppl=len(curves); YOU=7
d=mkd("fc_indiv"); fig,ax=vc.new_fig(); Nc=275; REV=180
for i in range(Nc):
    show=min(nppl, max(1,int(np.ceil((i/REV)*nppl)))) if i<REV else nppl
    ax.cla(); ax.axhline(0,color=MUTED,lw=1.0,ls='--')
    for k,c in enumerate(curves[:show]):
        if k==YOU: continue
        ax.semilogx(FREQ,c,color=CYAN,lw=1.3,alpha=0.42)
    ax.text(210,8.6,f"{show} real people  -  same direction, same sound",color=MUTED,fontsize=18,va='top')
    if show>YOU:
        ya=min(1.0,max(0.0,(i-120)/20.))
        ax.semilogx(FREQ,curves[YOU],color=AMBER,lw=3.4,alpha=ya)
        ax.text(240,6.4,"amber = one of these people (you)",color=AMBER,fontsize=16,va='top',alpha=ya)
    if i>REV:                                   # honest: differences grow with frequency (no 'identical below')
        pa=min(1.0,(i-REV)/28.)
        ax.annotate("",xy=(18000,5.6),xytext=(6500,5.6),arrowprops=dict(color=AMBER,arrowstyle="-|>",lw=2.2,alpha=0.85*pa))
        ax.text(240,5.0,"spread grows toward the highs - the folds of your ear",
                color=AMBER,fontsize=15,va='top',ha='left',alpha=pa)
    ax.set_xlim(200,20000); ax.set_ylim(-42,11); vc.khz(ax); ax.grid(alpha=0.35,which='major')
    ax.set_xlabel("pitch  (frequency)",fontsize=20,color=INK); ax.set_ylabel("tone colour  (dB)",fontsize=20,color=INK)
    vc.chrome(fig,"04d","INDIVIDUALITY","No two people's ears colour sound the same")
    fig.savefig(os.path.join(d,f"{i:05d}.png")); [a2.remove() for a2 in fig.axes if a2 is not ax]
plt.close(fig); print("fc_indiv done",nppl,"subjects")
print("ALL DONE")
