#!/usr/bin/env python3
"""Reworked ITD sweep (sign flipped to match intuition, semantic axis) +
cone of confusion 3D (fixed 3/4 camera, filled cone, corrected science: timing
AND loudness constant; head motion is the front/back tie-breaker = the payoff bridge)."""
import glob, os, numpy as np, h5py, math
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import correlate, savgol_filter
from scipy.interpolate import interp1d
from mpl_toolkits.mplot3d import Axes3D  # noqa
import vizchrome as vc
vc.style()
CYAN,AMBER,INK,MUTED,GRID = vc.CYAN,vc.AMBER,vc.INK,vc.MUTED,vc.GRID
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
azd=np.linspace(az.min(),az.max(),721)
# DISPLAY sign: right-side source -> UP (positive). raw: right source is negative, so negate.
itdd=-savgol_filter(interp1d(az,itd,kind='cubic',fill_value='extrapolate')(azd), 61, 3)
def itd_at(a):
    a=((a+180)%360)-180; return float(np.interp(a, azd, itdd))

# ===== clip 2b: animated ITD, source sweeps azimuth, right = up =====
dd=mkd("fa2_itd")
fig,axc=vc.new_fig(); axh=fig.add_axes([0.72,0.33,0.22,0.42]); axh.set_aspect('equal')
N=360
for i in range(N):
    a=(i/N)*360.0; aw=((a+180)%360)-180; val=itd_at(a)
    axc.cla(); axc.axhline(0,color=GRID,lw=1.2)
    axc.plot(azd,itdd,color=CYAN,lw=3); axc.fill_between(azd,itdd,0,color=CYAN,alpha=0.10)
    # zero-crossing markers: front (0) and back (+/-180)
    for z,lab in ((0,"front: 0 ms"),(180,"back: 0 ms"),(-180,"")):
        axc.plot([z],[0],'o',color=MUTED,ms=9)
        if lab: axc.annotate(lab,(z,0),color=MUTED,fontsize=14,xytext=(z-40,0.28))
    axc.annotate("same!",(180,0),color=AMBER,fontsize=15,xytext=(120,-0.33),
                 arrowprops=dict(color=AMBER,arrowstyle="->")); axc.plot([-180],[0],'o',color=MUTED,ms=9)
    axc.plot([aw],[val],'o',color=AMBER,ms=20); axc.plot([aw,aw],[0,val],color=AMBER,lw=1.5,ls=':')
    axc.set_xlim(-180,180); axc.set_ylim(-1.1,1.1); axc.set_xticks([-180,-90,0,90,180])
    axc.set_yticks([-0.5,0,0.5]); axc.grid(alpha=0.4)
    axc.set_xlabel("sound direction   (deg,  0 = front,  + = right)",fontsize=20,color=INK)
    axc.text(-205,0.95,"RIGHT ear\nfirst",color=CYAN,fontsize=15,va='top',ha='left')
    axc.text(-205,-0.55,"LEFT ear\nfirst",color=CYAN,fontsize=15,va='top',ha='left')
    if abs(val)<0.05: rd="both ears together"
    elif val>0: rd=f"right ear hears it {abs(val):.1f} ms sooner"
    else: rd=f"left ear hears it {abs(val):.1f} ms sooner"
    axc.text(aw+(14 if aw<120 else -14), val+(0.16 if val<0.8 else -0.18), rd,
             color=AMBER,fontsize=18,ha=('left' if aw<120 else 'right'),va='center')
    # inset head + orbiting source
    axh.cla(); axh.axis('off'); axh.set_xlim(-1.5,1.5); axh.set_ylim(-1.5,1.5)
    th=np.linspace(0,2*np.pi,80); axh.plot(0.62*np.cos(th),0.62*np.sin(th),color=CYAN,lw=2)
    axh.plot([0,0],[0.62,0.82],color=CYAN,lw=2)
    for sgn in (-1,1): axh.plot([sgn*0.62],[0],'o',color=CYAN,ms=9)
    ar=math.radians(a); sxx,syy=math.sin(ar)*1.25, math.cos(ar)*1.25
    near=0.62 if aw>0 else -0.62
    axh.plot([sxx,near],[syy,0],color=AMBER,lw=2); axh.plot([sxx,-near],[syy,0],color=MUTED,lw=1,ls=':')
    axh.plot([sxx],[syy],'o',color=AMBER,ms=15)
    vc.chrome(fig,"04a","TIMING","The nearer ear hears it first - by under a millisecond")
    fig.savefig(os.path.join(dd,f"{i:05d}.png")); [a2.remove() for a2 in fig.axes if a2 not in (axc,axh)]
plt.close(fig); print("fa2_itd done  display range",round(itdd.min(),2),round(itdd.max(),2))

# ===== clip 5: cone of confusion (fixed 3/4 camera, filled cone) =====
de=mkd("fe_cone"); fig=plt.figure(figsize=(19.2,10.8),dpi=100)
ax=fig.add_axes([0.0,0.04,1.0,0.80],projection='3d')
u=np.linspace(0,2*np.pi,24); v=np.linspace(0,np.pi,14)
sx=0.5*np.outer(np.cos(u),np.sin(v)); sy=0.5*np.outer(np.sin(u),np.sin(v)); sz=0.5*np.outer(np.ones_like(u),np.cos(v))
alpha=math.radians(55); xC=math.cos(alpha); rC=math.sin(alpha)
phi=np.linspace(0,2*np.pi,160); ringY=rC*np.cos(phi); ringZ=rC*np.sin(phi)
tt=np.linspace(0,1,2); PH,TT=np.meshgrid(phi,tt)
coneX=TT*xC; coneY=TT*rC*np.cos(PH); coneZ=TT*rC*np.sin(PH)
dots=[0,45,90,135,180,225,270,315]
Nc=300
for i in range(Nc):
    ax.cla(); ax.set_axis_off(); ax.set_box_aspect((1,1,0.85))
    ax.set_xlim(-1.2,1.2); ax.set_ylim(-1.2,1.2); ax.set_zlim(-1.0,1.0)
    ax.plot_surface(coneX,coneY,coneZ,color=AMBER,alpha=0.13,linewidth=0,shade=False)
    ax.plot_wireframe(sx,sy,sz,color=CYAN,alpha=0.10,linewidth=0.6)
    ax.plot([-1.15,1.15],[0,0],[0,0],color=CYAN,lw=2)
    ax.scatter([-0.5,0.5],[0,0],[0,0],color=CYAN,s=60)
    ax.text(-0.9,0,0.14,"L ear",color=MUTED,fontsize=13); ax.text(0.62,0,0.14,"R ear",color=MUTED,fontsize=13)
    ax.plot(np.full_like(phi,xC),ringY,ringZ,color=AMBER,lw=3)
    pr=(i/Nc)
    for base in dots:
        ph=math.radians(base)+pr*2*np.pi
        ax.scatter([xC],[rC*math.cos(ph)],[rC*math.sin(ph)],color=AMBER,s=110,edgecolor=INK,linewidth=0.5)
    ax.view_init(elev=17, azim=-58+5*math.sin(2*np.pi*i/Nc))   # fixed 3/4, never edge-on
    fig.text(0.5,0.90,"Every direction on this cone shares the SAME timing AND loudness",
             color=AMBER,fontsize=22,ha='center')
    fig.text(0.5,0.165,"A still head can only lean on the fragile spectral fingerprint  (front/back and up/down blur)",
             color=MUTED,fontsize=17,ha='center')
    vc.chrome(fig,"04c","THE CONE OF CONFUSION","But turn your head, and the timing flips front vs back - the missing cue")
    fig.savefig(os.path.join(de,f"{i:05d}.png")); [a2.remove() for a2 in fig.axes if a2 is not ax]
plt.close(fig); print("fe_cone done")
print("ALL DONE")
