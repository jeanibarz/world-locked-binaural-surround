"""Shared house-style chrome for the matplotlib crash-course clips.
Makes the plots read as the same film as the Pillow slides:
top-left section tag + bottom lower-third caption bar, house palette, kHz ticks,
ordered sequential color ramp. Accent rule: cyan = the true signal/cue,
amber = the problem / the one call-out marker. amber is NEVER a data series."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Rectangle

BG="#0a0e14"; PANEL="#121b24"; CYAN="#35e0d0"; AMBER="#f5b556"; INK="#e8eef0"; MUTED="#8a9aa3"; GRID="#1e2a33"

def style():
    plt.rcParams.update({
        "figure.facecolor":BG,"axes.facecolor":"none","savefig.facecolor":BG,
        "text.color":INK,"axes.labelcolor":INK,"xtick.color":MUTED,"ytick.color":MUTED,
        "axes.edgecolor":GRID,"grid.color":GRID,"axes.linewidth":1.6,
        "font.family":"DejaVu Sans Mono","font.size":18,
        "axes.spines.top":False,"axes.spines.right":False,
    })

# data axes live in this rect; chrome uses the top strip (tag) and bottom strip (caption)
MAIN=[0.085,0.26,0.885,0.60]

def new_fig():
    fig=plt.figure(figsize=(19.2,10.8),dpi=100)
    ax=fig.add_axes(MAIN)
    return fig,ax

_overlay=None
def chrome(fig, idx, label, caption):
    """Top-left section tag + bottom lower-third caption bar (drawn each frame)."""
    ax=fig.add_axes([0,0,1,1]); ax.set_axis_off(); ax.set_xlim(0,1); ax.set_ylim(0,1)
    ax.set_zorder(50)
    # section tag
    ax.text(0.035,0.955,idx,color=CYAN,fontsize=24,fontweight='bold',family='monospace',va='top')
    ax.text(0.085,0.952,label,color=MUTED,fontsize=19,family='monospace',va='top')
    # lower-third caption bar
    ax.add_patch(Rectangle((0.035,0.035),0.60,0.085,facecolor='black',alpha=0.62,zorder=51))
    ax.add_patch(Rectangle((0.035,0.035),0.0055,0.085,facecolor=CYAN,zorder=52))
    ax.text(0.055,0.078,caption,color=INK,fontsize=24,fontweight='bold',va='center',zorder=52)
    return ax

def ramp(n):
    cmap=mcolors.LinearSegmentedColormap.from_list("seq",["#35e0d0","#6fb8e6","#b98cff","#ff9ad0"])
    return [cmap(i/max(1,n-1)) for i in range(n)]

def khz(ax):
    t=[200,500,1000,2000,5000,10000,20000]
    ax.set_xticks(t); ax.set_xticklabels(["0.2k","0.5k","1k","2k","5k","10k","20k"])
