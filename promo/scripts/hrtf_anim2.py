#!/usr/bin/env python3
"""Reworked animated ITD (source sweeps azimuth) + cone-of-confusion 3D."""
import glob, os, numpy as np, h5py, math
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import correlate, savgol_filter
from scipy.interpolate import interp1d
from mpl_toolkits.mplot3d import Axes3D  # noqa

BG="#0a0e14"; PANEL="#121b24"; CYAN="#35e0d0"; AMBER="#f5b556"; INK="#e8eef0"; MUTED="#8a9aa3"; GRID="#1e2a33"
plt.rcParams.update({"figure.facecolor":BG,"axes.facecolor":BG,"savefig.facecolor":BG,
 "text.color":INK,"axes.labelcolor":INK,"xtick.color":MUTED,"ytick.color":MUTED,
 "axes.edgecolor":GRID,"grid.color":GRID,"font.family":"DejaVu Sans Mono","font.size":16})
OUT="/tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut"
SOFA=sorted(glob.glob('/home/jean/git/binaural-localization/data/ircam_sofa/IRC_*_R_44100.sofa'))
SR=44100.0
def mkd(n): p=os.path.join(OUT,n); os.makedirs(p,exist_ok=True); return p
d=h5py.File(SOFA[0],'r'); ir=np.array(d['Data.IR']); sp=np.array(d['SourcePosition']); d.close()
idx=np.where(np.abs(sp[:,1])<=1.0)[0]; az=sp[idx,0].copy(); az[az>180]-=360
o=np.argsort(az); az=az[o]; idx=idx[o]
itd=[]
for m in idx:
    L=ir[m,0,:1024].astype(float); R=ir[m,1,:1024].astype(float)
    c=np.abs(correlate(L,R,mode='full')); p=int(np.argmax(c))
    den=(c[p-1]-2*c[p]+c[p+1]) if 0<p<len(c)-1 else 0
    dd=0.5*(c[p-1]-c[p+1])/den if den else 0
    itd.append(((p-(len(L)-1))+dd)/SR*1e3)
itd=np.array(itd)
# smooth, dense
azd=np.linspace(az.min(),az.max(),721)
itdd=savgol_filter(interp1d(az,itd,kind='cubic',fill_value='extrapolate')(azd), 61, 3)
def itd_at(a):
    a=((a+180)%360)-180; return float(np.interp(a, azd, itdd))

# ===== clip 2b: animated ITD, source sweeps azimuth =====
dd=mkd("fa2_itd"); fig=plt.figure(figsize=(19.2,10.8),dpi=100)
axc=fig.add_axes([0.07,0.12,0.60,0.72]); axh=fig.add_axes([0.70,0.30,0.27,0.50]); axh.set_aspect('equal')
N=360
for i in range(N):
    a=(i/N)*360.0; aw=((a+180)%360)-180; val=itd_at(a)
    axc.cla(); axc.axhline(0,color=GRID,lw=1)
    axc.plot(azd,itdd,color=CYAN,lw=3); axc.fill_between(azd,itdd,0,color=CYAN,alpha=0.10)
    axc.plot([aw],[val],'o',color=AMBER,ms=20); axc.plot([aw,aw],[0,val],color=AMBER,lw=1.5,ls=':')
    axc.set_xlim(-180,180); axc.set_ylim(-1.1,1.1); axc.set_xticks([-180,-90,0,90,180]); axc.grid(alpha=0.5)
    axc.set_xlabel("sound direction   (deg,  0=front,  +=right)",fontsize=20)
    axc.set_ylabel("interaural time difference  (ms)",fontsize=20)
    axc.set_title("The time difference changes smoothly with direction",color=INK,fontsize=25,pad=16)
    lead = "both ears together" if abs(val)<0.05 else ("right ear leads" if val<0 else "left ear leads")
    axc.text(-170,0.98,f"ITD = {abs(val):.2f} ms\n{lead}",color=AMBER,fontsize=22,va='top')
    if abs(aw)<10 or abs(abs(aw)-180)<10:
        axc.text(-170,-0.75,"front and back ~ 0 ms\n(timing alone can't tell them apart)",color=MUTED,fontsize=16,va='top')
    # inset top-down head + orbiting source
    axh.cla(); axh.axis('off'); axh.set_xlim(-1.5,1.5); axh.set_ylim(-1.5,1.5)
    th=np.linspace(0,2*np.pi,80); axh.plot(0.62*np.cos(th),0.62*np.sin(th),color=CYAN,lw=2)
    axh.plot([0,0],[0.62,0.82],color=CYAN,lw=2)                 # front/nose
    for sgn,lab in ((-1,"L"),(1,"R")): axh.plot([sgn*0.62],[0],'o',color=CYAN_ if False else CYAN,ms=10)
    ar=math.radians(a); sxx,syy=math.sin(ar)*1.25, math.cos(ar)*1.25
    near = 0.62 if aw>0 else -0.62
    axh.plot([sxx,near],[syy,0],color=AMBER,lw=2)               # path to near ear
    axh.plot([sxx,-near],[syy,0],color=MUTED,lw=1,ls=':')
    axh.plot([sxx],[syy],'o',color=AMBER,ms=16); axh.text(sxx-0.2,syy+0.14,"sound",color=MUTED,fontsize=14)
    fig.savefig(os.path.join(dd,f"{i:05d}.png"))
plt.close(fig); print("fa2_itd done  itd range",round(itdd.min(),2),round(itdd.max(),2))

# ===== clip 5: cone of confusion (3D) =====
CYAN_=CYAN
de=mkd("fe_cone"); fig=plt.figure(figsize=(19.2,10.8),dpi=100)
ax=fig.add_axes([0.0,0.0,1.0,1.0],projection='3d')
u=np.linspace(0,2*np.pi,28); v=np.linspace(0,np.pi,16)
sx=0.55*np.outer(np.cos(u),np.sin(v)); sy=0.55*np.outer(np.sin(u),np.sin(v)); sz=0.55*np.outer(np.ones_like(u),np.cos(v))
alpha=math.radians(58); xC=math.cos(alpha); rC=math.sin(alpha)
phi=np.linspace(0,2*np.pi,160)
ringX=np.full_like(phi,xC); ringY=rC*np.cos(phi); ringZ=rC*np.sin(phi)
dots=[0, 45, 90, 135, 180, 225, 270, 315]
Nc=330
for i in range(Nc):
    ax.cla(); ax.set_axis_off(); ax.set_box_aspect((1,1,0.8))
    ax.set_xlim(-1.3,1.3); ax.set_ylim(-1.3,1.3); ax.set_zlim(-1.1,1.1)
    ax.plot_wireframe(sx,sy,sz,color=CYAN,alpha=0.14,linewidth=0.6)
    ax.plot([-1.25,1.25],[0,0],[0,0],color=CYAN,lw=2)          # interaural axis
    ax.scatter([-0.62,0.62],[0,0],[0,0],color=CYAN,s=70)
    ax.text(-0.95,0,0.16,"L ear",color=MUTED,fontsize=13); ax.text(0.7,0,0.16,"R ear",color=MUTED,fontsize=13)
    # cone surface lines + ring
    for ph in np.linspace(0,2*np.pi,24,endpoint=False):
        ax.plot([0,xC],[0,rC*np.cos(ph)],[0,rC*np.sin(ph)],color=AMBER,alpha=0.10,lw=1)
    ax.plot(ringX,ringY,ringZ,color=AMBER,lw=3)
    pr=(i/Nc)                                                   # dots travel around the ring
    for base in dots:
        ph=math.radians(base)+pr*2*np.pi
        ax.scatter([xC],[rC*math.cos(ph)],[rC*math.sin(ph)],color=AMBER,s=120,edgecolor=INK,linewidth=0.5)
    ax.view_init(elev=16, azim=-70+18*math.sin(2*np.pi*i/Nc))
    fig.text(0.5,0.92,"The cone of confusion",color=INK,fontsize=30,ha='center',weight='bold')
    fig.text(0.5,0.865,"every direction on this cone has the SAME time difference",color=AMBER,fontsize=20,ha='center')
    fig.text(0.5,0.12,"front / back and up / down become ambiguous -> only the spectral fingerprint (HRTF) can break the tie",
             color=MUTED,fontsize=18,ha='center')
    fig.text(0.5,0.07,"and spectral cues are weaker and less reliable than timing -> this is where localisation is hardest",
             color=MUTED,fontsize=18,ha='center')
    fig.savefig(os.path.join(de,f"{i:05d}.png"))
plt.close(fig); print("fe_cone done")
print("ALL DONE")
