#!/usr/bin/env python3
"""Real HRTF visuals from IRCAM LISTEN SOFA data, styled to the house palette."""
import glob, numpy as np, h5py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import correlate

BG="#0a0e14"; PANEL="#121b24"; CYAN="#35e0d0"; AMBER="#f5b556"; INK="#e8eef0"; MUTED="#8a9aa3"; GRID="#1e2a33"
plt.rcParams.update({
    "figure.facecolor":BG,"axes.facecolor":BG,"savefig.facecolor":BG,
    "text.color":INK,"axes.labelcolor":INK,"xtick.color":MUTED,"ytick.color":MUTED,
    "axes.edgecolor":GRID,"grid.color":GRID,"font.family":"DejaVu Sans Mono","font.size":15,
})
SOFA=sorted(glob.glob('/home/jean/git/binaural-localization/data/ircam_sofa/IRC_*_R_44100.sofa'))
OUT="/tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut"

def load(f):
    d=h5py.File(f,'r'); ir=np.array(d['Data.IR']); sp=np.array(d['SourcePosition'])
    sr=float(np.array(d['Data.SamplingRate']).flatten()[0]); d.close(); return ir,sp,sr

def horiz_idx(sp, tol=1.0):
    el=sp[:,1]; return np.where(np.abs(el)<=tol)[0]

# ---------- 1) ITD vs azimuth (real, cross-correlation) ----------
ir,sp,sr=load(SOFA[0])
hi=horiz_idx(sp); az=sp[hi,0].copy(); az[az>180]-=360        # -180..180
order=np.argsort(az); az=az[order]; hi=hi[order]
itd=[]
for m in hi:
    L=ir[m,0,:1024]; R=ir[m,1,:1024]
    c=correlate(L,R,mode='full'); lag=np.argmax(np.abs(c))-(len(L)-1)
    itd.append(lag/sr*1e3)
itd=np.array(itd)
fig,ax=plt.subplots(figsize=(12.8,7.2),dpi=100)
ax.axhline(0,color=GRID,lw=1)
ax.plot(az,itd,color=CYAN,lw=3)
ax.fill_between(az,itd,0,color=CYAN,alpha=0.12)
ax.set_xlabel("sound direction  (degrees,  0 = front,  + = right)")
ax.set_ylabel("interaural time difference  (ms)")
ax.set_title("The ear nearer the sound hears it first", color=INK, fontsize=20, pad=16)
ax.set_xlim(-180,180); ax.set_xticks([-180,-90,0,90,180]); ax.grid(alpha=0.5)
ax.annotate("right ear first", (90, np.interp(90,az,itd)), color=AMBER, fontsize=14,
            xytext=(96,0.45), arrowprops=dict(color=AMBER,arrowstyle="->"))
fig.tight_layout(); fig.savefig(f"{OUT}/plot_itd.png"); plt.close(fig)
print("itd range ms:",round(itd.min(),2),round(itd.max(),2))

# ---------- 2) HRTF magnitude spectrum changes with direction (one subject) ----------
def mag_db(hrir, n=512):
    w=np.hanning(len(hrir[:n])); H=np.fft.rfft(hrir[:n]*w, 2048)
    f=np.fft.rfftfreq(2048, 1/sr); return f, 20*np.log10(np.abs(H)+1e-6)
fig,ax=plt.subplots(figsize=(12.8,7.2),dpi=100)
dirs=[(0,"front"),(45,"right 45"),(90,"right 90"),(135,"behind-right"),(180,"behind")]
cols=[CYAN,"#7fe0ff","#f5b556","#ff9a8b","#b98cff"]
for (a,lab),c in zip(dirs,cols):
    j=hi[np.argmin(np.abs(az-a))]; f,m=mag_db(ir[j,1])   # right ear
    ax.semilogx(f,m,color=c,lw=2.4,label=lab)
ax.set_xlim(200,20000); ax.set_ylim(-35,20)
ax.set_xlabel("frequency  (Hz)"); ax.set_ylabel("right-ear gain  (dB)")
ax.set_title("Your ears stamp a direction-dependent 'tonal fingerprint'", color=INK, fontsize=20, pad=16)
ax.grid(alpha=0.4, which='both'); leg=ax.legend(facecolor=PANEL,edgecolor=GRID,labelcolor=INK,loc='lower left')
fig.tight_layout(); fig.savefig(f"{OUT}/plot_spectra_dir.png"); plt.close(fig)

# ---------- 3) individuality: same direction, many real people ----------
fig,ax=plt.subplots(figsize=(12.8,7.2),dpi=100)
A=45.0; cnt=0
for f_ in SOFA[:28]:
    try:
        ir2,sp2,sr2=load(f_); hi2=horiz_idx(sp2); az2=sp2[hi2,0].copy(); az2[az2>180]-=360
        j=hi2[np.argmin(np.abs(az2-A))]; f,m=mag_db(ir2[j,1])
        ax.semilogx(f,m,color=CYAN,lw=1.1,alpha=0.5); cnt+=1
    except Exception: pass
ax.set_xlim(200,20000); ax.set_ylim(-40,22)
ax.set_xlabel("frequency  (Hz)"); ax.set_ylabel("right-ear gain  (dB)")
ax.set_title(f"Same direction, {cnt} different real people — every pair of ears is unique",
             color=INK, fontsize=20, pad=16)
ax.grid(alpha=0.4, which='both')
ax.text(230,17,"each line = one person's HRTF\n(IRCAM LISTEN database)",color=MUTED,fontsize=13,va='top')
fig.tight_layout(); fig.savefig(f"{OUT}/plot_individuality.png"); plt.close(fig)
print("individuality subjects:",cnt)
print("DONE")
