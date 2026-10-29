#!/usr/bin/env python3
"""Animated HRTF sequences from real IRCAM SOFA data (frames -> dirs).
v2: house chrome, de-jargoned axes, median+band individuality, sequential colours."""
import glob, os, numpy as np, h5py
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d
import vizchrome as vc
vc.style()
CYAN,FOCAL,AMBER,INK,MUTED,LABEL,GRID = vc.CYAN,vc.FOCAL,vc.AMBER,vc.INK,vc.MUTED,vc.LABEL,vc.GRID
OUT=os.environ.get("PROMO_OUT", os.getcwd())
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
# Plot the DIRECTIONAL transfer function: each direction's spectrum MINUS the
# direction-averaged (diffuse-field) spectrum. Now 0 dB honestly means "same as the
# average over all directions", so the curve reads as true deviation from neutral
# (the raw |FFT| 0 dB line was physically arbitrary and the data never sat near it).
M=np.stack([octave_smooth(magdb(ir0[m,1])) for m in idx0],axis=0)
diffuse=M.mean(axis=0)
D=M-diffuse
fI=interp1d(az0,D,axis=0,bounds_error=False,fill_value=(D[0],D[-1]))
d=mkd("fb_spec")
fig,ax=vc.new_fig(); ins=fig.add_axes([0.135,0.30,0.12,0.18]); ins.set_aspect('equal')
YLO,YHI=-20,20
Nb=450          # more frames -> smoother sweep under the ~2.5x VO stretch
for i in range(Nb):
    a=150*np.sin(2*np.pi*i/Nb); cur=fI(a)
    ax.cla(); ax.semilogx(FREQ,cur,color=CYAN,lw=3); ax.fill_between(FREQ,cur,0,color=CYAN,alpha=0.07)
    ax.axhline(0,color=MUTED,lw=1.2,ls='--'); ax.text(210,0.9,"flat = average over all directions",color=MUTED,fontsize=14,va='bottom')
    ax.set_xlim(200,20000); ax.set_ylim(YLO,YHI); ax.set_yticks([-20,-10,0,10,20]); vc.khz(ax); ax.grid(alpha=0.35,which='major')
    ax.set_xlabel("frequency  (Hz)",fontsize=21,color=INK)
    ax.set_ylabel("tone colour  (dB vs average)",fontsize=21,color=INK)
    side = "front" if abs(a)<8 else (f"{abs(a):.0f} deg right" if a>0 else f"{abs(a):.0f} deg left")
    ax.text(210,YHI-1.3,f"sound at: {side}",color=AMBER,fontsize=23,va='top')
    # inset: head-from-above with ear dots + front tick (shared grammar with the ITD inset),
    # on an opaque backing so it floats clear of the data curve
    ins.cla(); ins.axis('off'); ins.set_xlim(-1.5,1.5); ins.set_ylim(-1.5,1.5)
    ins.add_patch(plt.Rectangle((-1.5,-1.5),3,3,facecolor='#0a0e14',edgecolor=GRID,lw=1.2,alpha=0.9,zorder=0))
    th=np.linspace(0,2*np.pi,60); ins.plot(np.cos(th),np.sin(th),color=CYAN,lw=2); ins.plot([0,0],[1,1.3],color=CYAN,lw=2)
    for sgn in (-1,1): ins.plot([sgn],[0],'o',color=CYAN,ms=6)
    ar=np.radians(a); ins.plot([np.sin(ar)*1.22],[np.cos(ar)*1.22],'o',color=AMBER,ms=13)
    # name the payoff exactly when the VO says "...your H R T F" (end of the segment)
    if i/Nb>0.84:
        la=min(1.0,(i/Nb-0.84)/0.08)
        ax.text(0.97,0.90,"timing + colour = one filter: your HRTF",transform=ax.transAxes,
                color=FOCAL,fontsize=25,fontweight='bold',ha='right',va='top',alpha=la,
                bbox=dict(boxstyle='round,pad=0.45',fc='#0a0e14',ec=FOCAL,alpha=la))
    vc.chrome(fig,"","TONE COLOUR","Same sound, new direction — new tone colour")
    fig.savefig(os.path.join(d,f"{i:05d}.png")); [a2.remove() for a2 in fig.axes if a2 not in (ax,ins)]
plt.close(fig); print("fb_spec done")

# ===== clip 4: individuality -> many real people, drawn one by one =====
# Level-align every subject (subtract their own broadband mean over 200-1500 Hz)
# BEFORE plotting. The raw spectra carry a ~33 dB span of pure broadband gain
# (a measurement/EQ offset) that swamps the real pinna differences and makes the
# spread look flat with frequency -- the OPPOSITE of the locked VO. Aligned, the
# inter-subject spread rises monotonically with frequency (SD ~1.5 dB low -> ~6 dB
# at 12 kHz), which is the true "agree low, scatter high" pinna story the VO tells.
LOBAND=(FREQ>=200)&(FREQ<=1500)
curves=[]
for f_ in SOFA[:30]:
    try:
        ir2,sp2=load(f_); idx2,az2=horiz(sp2); j=idx2[np.argmin(np.abs(az2-45))]
        c=octave_smooth(magdb(ir2[j,1]),frac=6)   # light smoothing keeps the pinna notches
        curves.append(c-c[LOBAND].mean())
    except Exception: pass
nppl=len(curves); YOU=7
C=np.stack(curves,axis=0); med=np.median(C,axis=0)
p10=np.percentile(C,10,axis=0); p90=np.percentile(C,90,axis=0)
d=mkd("fc_indiv"); fig,ax=vc.new_fig(); Nc=275; REV=170
for i in range(Nc):
    show=min(nppl, max(1,int(np.ceil((i/REV)*nppl)))) if i<REV else nppl
    ax.cla(); ax.axhline(0,color=MUTED,lw=1.0,ls='--')
    for k in range(show):
        if k==YOU: continue
        ax.semilogx(FREQ,curves[k],color=CYAN,lw=1.1,alpha=0.16)
    ax.text(230,12.4,f"{show} real people  -  same direction, same sound",color=LABEL,fontsize=19,va='top')
    # median + 10-90th percentile band: "scatter" becomes a band that visibly widens with frequency
    if i>REV-45:
        ba=min(1.0,max(0.0,(i-(REV-45))/30.))
        ax.fill_between(FREQ,p10,p90,color=CYAN,alpha=0.16*ba)
        ax.semilogx(FREQ,med,color=CYAN,lw=2.0,alpha=0.85*ba)
    if show>YOU:
        ya=min(1.0,max(0.0,(i-105)/20.))
        ax.semilogx(FREQ,curves[YOU],color=FOCAL,lw=3.4,alpha=ya)
        ax.text(230,9.8,"bright line = one of them (you)",color=FOCAL,fontsize=17,va='top',alpha=ya)
    if i>REV:                                   # honest: spread grows with frequency, strongest in the pinna region
        pa=min(1.0,(i-REV)/28.)
        ax.axvline(6000,color=MUTED,ls=':',lw=1.4,alpha=0.7*pa)
        ax.text(6250,-21.5,"pinna region",color=MUTED,fontsize=13,va='bottom',alpha=pa)
        ax.annotate("",xy=(18000,7.2),xytext=(6800,7.2),arrowprops=dict(color=FOCAL,arrowstyle="-|>",lw=2.2,alpha=0.85*pa))
        ax.text(2200,7.0,"spread widens up high - the folds of your ear",
                color=INK,fontsize=15,va='center',ha='left',alpha=pa)
    ax.set_xlim(200,20000); ax.set_ylim(-24,15); vc.khz(ax); ax.grid(alpha=0.35,which='major')
    ax.set_xlabel("frequency  (Hz)",fontsize=21,color=INK); ax.set_ylabel("tone colour  (dB, level-aligned)",fontsize=21,color=INK)
    vc.chrome(fig,"","INDIVIDUALITY","Everyone's ears colour sound a little differently")
    fig.savefig(os.path.join(d,f"{i:05d}.png")); [a2.remove() for a2 in fig.axes if a2 is not ax]
plt.close(fig); print("fc_indiv done",nppl,"subjects")
print("ALL DONE")
