#!/usr/bin/env python3
"""Render on-brand motion-graphic frames (Pillow) for the promo. PoC: beat 1."""
import sys, os, math
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 30

BG      = (10, 14, 20)
PANEL   = (18, 27, 36)
CYAN    = (53, 224, 208)
CYAN_D  = (26, 92, 88)
INK     = (232, 238, 240)
MUTED   = (120, 140, 150)
AMBER   = (245, 181, 86)

FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FM = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
def font(path, size): return ImageFont.truetype(path, size)

def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)

def lerp(a, b, t): return a + (b - a) * t

# --- reusable background: faint dot grid + vignette -------------------------
def make_bg():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img, "RGBA")
    step = 48
    for y in range(0, H, step):
        for x in range(0, W, step):
            d.ellipse([x-1, y-1, x+1, y+1], fill=(255, 255, 255, 8))
    # vignette
    vig = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vig)
    vd.ellipse([-W*0.3, -H*0.3, W*1.3, H*1.3], fill=255)
    vig = vig.point(lambda p: int((255 - p) * 0.55))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(dark, img, vig)
    return img

BASE_BG = make_bg()

def glow_dot(draw_img, cx, cy, r, color, intensity=1.0):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for rr, a in ((r*3.0, 26), (r*2.0, 48), (r*1.3, 90)):
        ld.ellipse([cx-rr, cy-rr, cx+rr, cy+rr],
                   fill=(color[0], color[1], color[2], int(a*intensity)))
    ld.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(color[0], color[1], color[2], 255))
    ld.ellipse([cx-r*0.4, cy-r*0.4, cx+r*0.4, cy+r*0.4], fill=(240, 255, 253, 255))
    draw_img.alpha_composite(layer)

def text_center(draw, cx, y, s, fnt, fill, alpha=255):
    bb = draw.textbbox((0, 0), s, font=fnt)
    w = bb[2] - bb[0]
    draw.text((cx - w/2, y), s, font=fnt, fill=fill + (alpha,))

def draw_headphone(d, cx, cy, a):
    col = MUTED + (a,)
    d.arc([cx-26, cy-26, cx+26, cy+8], start=180, end=360, fill=col, width=6)
    d.rounded_rectangle([cx-32, cy-6, cx-18, cy+22], radius=5, fill=col)
    d.rounded_rectangle([cx+18, cy-6, cx+32, cy+22], radius=5, fill=col)

def lower_third(base, s, fnt, appear):
    if appear <= 0: return
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bb = d.textbbox((0, 0), s, font=fnt); tw = bb[2]-bb[0]; th = bb[3]-bb[1]
    pad = 26; bx0 = W/2 - tw/2 - pad; bx1 = W/2 + tw/2 + pad
    by0 = H - 150; by1 = by0 + th + pad*1.4
    a = int(smooth(appear) * 255)
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=10, fill=(0, 0, 0, int(a*0.62)))
    d.rectangle([bx0, by0, bx0+4, by1], fill=CYAN + (a,))
    d.text((W/2 - tw/2, by0 + pad*0.55), s, font=fnt, fill=INK + (a,))
    base.alpha_composite(layer)

# --- beat 1: stereo, sound only moves between the two ears (stays in head) --
def render_beat1(outdir, nframes):
    os.makedirs(outdir, exist_ok=True)
    f_eye = font(FM, 30); f_lab = font(FB, 30); f_cap = font(FB, 52); f_big = font(FB, 40); f_sm = font(FM, 24)
    cx, cy, R = 960, 492, 152
    amp = R * 0.80                                   # dot stays between the ears
    trail = []
    for i in range(nframes):
        t = i / FPS
        base = BASE_BG.convert("RGBA").copy()
        d = ImageDraw.Draw(base, "RGBA")

        intro = smooth(t / 0.6)
        ha = int(intro * 255)
        eye_a = int(smooth((t-0.3)/0.6) * 255)
        d.text((120, 90), "01", font=font(FM,34), fill=CYAN + (eye_a,))
        d.text((188, 92), "PLAIN  STEREO", font=f_eye, fill=MUTED + (eye_a,))
        # headphone cue (top-right)
        hp_a = int(smooth((t-0.2)/0.7) * 220)
        draw_headphone(d, 1706, 100, hp_a)
        d.text((1548, 138), "headphones on", font=f_sm, fill=MUTED + (hp_a,))

        s = lerp(0.92, 1.0, intro); r = R * s; a = amp * s
        # internal L<->R dashed axis, only between the ears (inside the head)
        seg = 20; x = cx - a
        while x < cx + a:
            d.line([x, cy, min(x+seg*0.55, cx+a), cy], fill=CYAN_D + (int(ha*0.9),), width=3)
            x += seg
        # head (top-down): panel fill + cyan ring, nose at front (up)
        d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=PANEL + (ha,), outline=CYAN + (ha,), width=4)
        d.polygon([(cx, cy-r-20), (cx-16, cy-r+6), (cx+16, cy-r+6)], fill=CYAN + (ha,))
        # headphone cups = ears, labelled L / R just under each cup
        for ex, lab in ((cx-r, "L"), (cx+r, "R")):
            d.rounded_rectangle([ex-16, cy-30, ex+16, cy+30], radius=8, fill=CYAN_D + (ha,), outline=CYAN + (ha,), width=2)
            tb = d.textbbox((0, 0), lab, font=f_lab)
            d.text((ex-(tb[2]-tb[0])/2, cy+40), lab, font=f_lab, fill=MUTED + (ha,))

        # the trapped sound dot: moves ear-to-ear only, never outside the head
        if t > 0.8:
            tt = t - 0.8
            px = cx + a * math.sin(tt * 1.4)
            trail.append(px); trail[:] = trail[-10:]
            for k, txp in enumerate(trail[:-1]):
                aa = int(45 * (k+1)/len(trail)); rr = 6 * (k+1)/len(trail)
                d.ellipse([txp-rr, cy-rr, txp+rr, cy+rr], fill=CYAN + (aa,))
            glow_dot(base, px, cy, 14, CYAN)

        text_center(d, cx, 212, "It only moves between your two ears.", f_big, MUTED,
                    int(smooth((t-0.4)/0.7) * 255))
        lower_third(base, "On headphones, sound is stuck inside your head", f_cap, (t-1.0)/0.6)
        base.convert("RGB").save(os.path.join(outdir, f"{i:05d}.png"))
    print(f"beat1: {nframes} frames -> {outdir}")

def env(p, t0, t1, t2, t3):
    if p < t0: return 0.0
    if p < t1: return smooth((p-t0)/(t1-t0))
    if p < t2: return 1.0
    if p < t3: return 1.0 - smooth((p-t2)/(t3-t2))
    return 0.0

def head_group(d, cx, cy, r, ha, nose=0.0):
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=PANEL+(ha,), outline=CYAN+(ha,), width=4)
    tx = cx + (r+22)*math.sin(nose); ty = cy - (r+22)*math.cos(nose)
    bx = cx + r*math.sin(nose); by = cy - r*math.cos(nose)
    px, py = math.cos(nose), math.sin(nose)      # perp
    d.polygon([(tx, ty), (bx-16*px, by-16*py), (bx+16*px, by+16*py)], fill=CYAN+(ha,))
    for s in (-1, 1):                             # ear cups
        ex = cx + r*math.sin(nose+s*math.pi/2); ey = cy - r*math.cos(nose+s*math.pi/2)
        d.ellipse([ex-11, ey-22, ex+11, ey+22], fill=CYAN_D+(ha,), outline=CYAN+(ha,), width=2)

# --- beat 2: binaural, a source orbits the head ----------------------------
def render_beat2(outdir, nframes):
    os.makedirs(outdir, exist_ok=True)
    f_eye=font(FM,30); f_cap=font(FB,52); f_big=font(FB,40); f_sm=font(FM,24)
    cx, cy, R = 960, 500, 128
    Rx, Ry = 340, 250
    for i in range(nframes):
        t=i/FPS; base=BASE_BG.convert("RGBA").copy(); d=ImageDraw.Draw(base,"RGBA")
        intro=smooth(t/0.6); ha=int(intro*255)
        ea=int(smooth((t-0.3)/0.6)*255)
        d.text((120,90),"02",font=font(FM,34),fill=CYAN+(ea,))
        d.text((188,92),"BINAURAL",font=f_eye,fill=MUTED+(ea,))
        hp=int(smooth((t-0.2)/0.7)*220); draw_headphone(d,1706,100,hp); d.text((1548,138),"headphones on",font=f_sm,fill=MUTED+(hp,))
        # faint orbit guide
        d.ellipse([cx-Rx,cy-Ry,cx+Rx,cy+Ry],outline=CYAN_D+(int(ha*0.5),),width=2)
        head_group(d,cx,cy,R*lerp(0.92,1,intro),ha)
        # ear level meters
        phi = -math.pi/2 + 0.30*max(0.0,t-1.0)     # start front, orbit
        sx=cx+Rx*math.cos(phi); sy=cy+Ry*math.sin(phi)
        if t>1.0:
            lvlR=max(0.06,0.5+0.5*((sx-cx)/Rx)); lvlL=max(0.06,0.5-0.5*((sx-cx)/Rx))
            for bx,lv,lab in ((250,lvlL,"L EAR"),(1670,lvlR,"R EAR")):
                d.rounded_rectangle([bx-34,cy-150,bx+34,cy+150],radius=10,outline=MUTED+(int(ha*0.6),),width=2)
                hh=int(290*min(1,lv)); d.rounded_rectangle([bx-28,cy+144-hh,bx+28,cy+144],radius=8,fill=CYAN+(int(ha*0.85),))
                d.text((bx-34,cy+164),lab,font=f_sm,fill=MUTED+(ha,))
            # connecting ray + source dot (draw behind head when in back half)
            d.line([cx,cy,sx,sy],fill=CYAN_D+(130,),width=2)
            glow_dot(base,sx,sy,15,(140,240,255))
            d.text((sx-44,sy-46),"a sound",font=f_sm,fill=MUTED+(200,))
        tag_a=int(smooth((t-6)/0.8)*255) if t>6 else 0
        if tag_a: d.text((cx-150,cy+R+70),"your two ears, recreated = H R T F",font=f_sm,fill=CYAN+(tag_a,))
        text_center(d,cx,196,"Now it can come from anywhere around you.",f_big,MUTED,int(smooth((t-0.4)/0.7)*255))
        lower_third(base,"Binaural recreates what each ear would really hear",f_cap,(t-1.0)/0.6)
        base.convert("RGB").save(os.path.join(outdir,f"{i:05d}.png"))
    print(f"beat2: {nframes} frames")

# --- beat 3: the catch -> (parallax) -> world-lock -------------------------
SPK = [math.radians(a) for a in (-30, 30, -110, 110, 180)]
def _ring(d, cx, cy, Rr, gamma, ha, good):
    col = CYAN if good else AMBER
    d.ellipse([cx-Rr,cy-Ry3,cx+Rr,cy+Ry3],outline=col+(int(ha*0.35),),width=2)
    for b in SPK:
        ang=b+gamma; x=cx+Rr*math.sin(ang); y=cy-Ry3*math.cos(ang)
        d.rounded_rectangle([x-16,y-14,x+16,y+14],radius=5,fill=PANEL+(ha,),outline=col+(ha,),width=2)
        d.ellipse([x-4,y-4,x+4,y+4],fill=col+(ha,))
Ry3=250
def render_beat3(outdir, nframes):
    os.makedirs(outdir, exist_ok=True)
    f_eye=font(FM,30); f_cap=font(FB,50); f_big=font(FB,38); f_sm=font(FM,24)
    cx, cy, R, Rr = 960, 510, 120, 330
    for i in range(nframes):
        t=i/FPS; p=i/max(1,nframes-1)
        base=BASE_BG.convert("RGBA").copy(); d=ImageDraw.Draw(base,"RGBA")
        d.text((120,90),"03",font=font(FM,34),fill=CYAN+(220,))
        d.text((188,92),"THE MISSING CUE",font=f_eye,fill=MUTED+(220,))
        aA=env(p,0,0.03,0.27,0.32); aB=env(p,0.30,0.35,0.52,0.57); aC=env(p,0.55,0.60,1.01,1.02)
        swing=math.sin(t*1.6)*math.radians(28)
        # A: speakers glued to head -> rotate together (bad / amber)
        if aA>0.01:
            ha=int(aA*255); _ring(d,cx,cy,Rr,swing,ha,good=False); head_group(d,cx,cy,R,ha,nose=swing)
            text_center(d,cx,200,"Turn your head and the whole scene turns with you.",f_big,AMBER,ha)
        # B: parallax — a real object stays put when you move
        if aB>0.01:
            ha=int(aB*255); sway=math.sin(t*1.3)*120
            # mug (world-fixed) at centre-top
            mx,my=cx,cy-40
            d.rounded_rectangle([mx-46,my-54,mx+46,my+54],radius=10,fill=PANEL+(ha,),outline=CYAN+(ha,),width=3)
            d.arc([mx+36,my-34,mx+96,my+34],start=300,end=60,fill=CYAN+(ha,),width=6)
            d.text((mx-150,my-120),"real object — stays put",font=f_sm,fill=CYAN+(ha,))
            # viewer eye (sways), sightlines to mug
            ey=cy+230; ex=cx+sway
            d.line([ex,ey,mx,my],fill=CYAN_D+(ha,),width=2)
            glow_dot(base,ex,ey,12,(140,240,255)); d.text((ex-26,ey+22),"you",font=f_sm,fill=MUTED+(ha,))
            # smudge moves WITH you (fake)
            d.ellipse([ex-60,ey-150-0,ex-20,ey-110],fill=AMBER+(int(ha*0.6),))
            d.text((ex-70,ey-180),"smudge — moves with you",font=f_sm,fill=AMBER+(ha,))
        # C: world-locked — ring fixed, head rotates inside (good / cyan)
        if aC>0.01:
            ha=int(aC*255); _ring(d,cx,cy,Rr,0.0,ha,good=True); head_group(d,cx,cy,R,ha,nose=swing)
            # facing cone from head
            cone=Image.new("RGBA",(W,H),(0,0,0,0)); cd=ImageDraw.Draw(cone)
            L=Rr+10; a1=swing-math.radians(20); a2=swing+math.radians(20)
            cd.polygon([(cx,cy),(cx+L*math.sin(a1),cy-L*math.cos(a1)),(cx+L*math.sin(a2),cy-L*math.cos(a2))],fill=CYAN+(int(ha*0.18),))
            base.alpha_composite(cone)
            text_center(d,cx,200,"World-locked: the speakers stay put. Only you move.",f_big,CYAN,ha)
            if p>0.86:
                hy=int(smooth((p-0.86)/0.08)*255)
                lower_third(base,"The bet: this is what gets sound out of your head  (hypothesis, now testing)",font(FB,40),(p-0.86)/0.06)
        # phase captions
        if aA>0.5: lower_third(base,"Binaural is static — turn your head, the scene turns too",f_cap,aA)
        elif aB>0.5: lower_third(base,"A real sound stays put as you move — that is the cue",f_cap,aB)
        base.convert("RGB").save(os.path.join(outdir,f"{i:05d}.png"))
    print(f"beat3: {nframes} frames")

# --- beat 5: signal-flow diagram -------------------------------------------
def render_beat5(outdir, nframes):
    os.makedirs(outdir, exist_ok=True)
    f_eye=font(FM,30); f_cap=font(FB,48); f_box=font(FB,30); f_sm=font(FM,23)
    boxes=[("ANY APP","game · film · music"),("VB-AUDIO\nCABLE","virtual device"),
           ("RUST ENGINE","head-track · convolve · EQ"),("YOUR\nHEADPHONES","any pair")]
    n=len(boxes); bw,bh=300,150; gap=(W-120*2-n*bw)/(n-1); y0=470
    xs=[120+i*(bw+gap) for i in range(n)]
    for i in range(nframes):
        t=i/FPS; base=BASE_BG.convert("RGBA").copy(); d=ImageDraw.Draw(base,"RGBA")
        d.text((120,90),"05",font=font(FM,34),fill=CYAN+(220,)); d.text((188,92),"UNDER THE HOOD",font=f_eye,fill=MUTED+(220,))
        for j,(x,(t1,t2)) in enumerate(zip(xs,boxes)):
            a=smooth((t-0.4-j*0.5)/0.6); ha=int(a*255)
            if ha<=0: continue
            hot = boxes[j][0].startswith("RUST")
            col=CYAN if hot else MUTED
            d.rounded_rectangle([x,y0,x+bw,y0+bh],radius=14,fill=PANEL+(ha,),outline=col+(ha,),width=3)
            lines=t1.split("\n")
            for k,ln in enumerate(lines):
                tb=d.textbbox((0,0),ln,font=f_box); d.text((x+bw/2-(tb[2]-tb[0])/2,y0+34+k*34),ln,font=f_box,fill=INK+(ha,))
            tb=d.textbbox((0,0),t2,font=f_sm); d.text((x+bw/2-(tb[2]-tb[0])/2,y0+bh-30),t2,font=f_sm,fill=MUTED+(ha,))
            if j<n-1:
                ax=x+bw; axe=xs[j+1]; ay=y0+bh/2
                aa=int(smooth((t-0.7-j*0.5)/0.5)*255)
                d.line([ax+8,ay,axe-20,ay],fill=CYAN_D+(aa,),width=3)
                d.polygon([(axe-20,ay-8),(axe-6,ay),(axe-20,ay+8)],fill=CYAN+(aa,))
                # travelling packet
                fr=((t-0.7-j*0.5)%1.0)
                if 0<fr<1 and aa>0:
                    pxx=ax+8+(axe-28-(ax+8))*fr; glow_dot(base,pxx,ay,7,(140,240,255),0.8)
        text_center(d,cx_,196,"Any app in, head-tracked binaural out, on any headphones.",f_big5,MUTED,int(smooth((t-0.4)/0.7)*255))
        d.text((120,y0+bh+70),"Rust core · about 5x real time on CPU · no GPU",font=f_sm,fill=MUTED+(int(smooth((t-2)/0.8)*220) if t>2 else 0,))
        lower_third(base,"Rust engine · VB-Audio Cable in · your headphones out · live EQ",f_cap,(t-1.0)/0.6)
        base.convert("RGB").save(os.path.join(outdir,f"{i:05d}.png"))
    print(f"beat5: {nframes} frames")
cx_=960; f_big5=ImageFont.truetype(FB,38)

# --- beat 6: end card ------------------------------------------------------
def render_beat6(outdir, nframes):
    os.makedirs(outdir, exist_ok=True)
    f_title=font(FB,74); f_tag=font(FM,30); f_cta=font(FB,40); f_sm=font(FM,26)
    icon=None
    ip="/home/jean/git/world-locked-binaural-surround/app-icon-1024.png"
    if os.path.exists(ip):
        icon=Image.open(ip).convert("RGBA").resize((230,230))
    for i in range(nframes):
        t=i/FPS; base=BASE_BG.convert("RGBA").copy(); d=ImageDraw.Draw(base,"RGBA")
        ia=smooth(t/0.7)
        if icon is not None:
            glow_dot(base,960,300,110,CYAN,0.5*ia)
            tmp=icon.copy(); tmp.putalpha(icon.getchannel("A").point(lambda v:int(v*ia)))
            base.alpha_composite(tmp,(960-115,300-115))
        text_center(d,960,470,"World-Locked Binaural Surround",f_title,INK,int(smooth((t-0.4)/0.7)*255))
        text_center(d,960,560,"Head-tracked binaural surround, in software.",f_tag,MUTED,int(smooth((t-0.7)/0.7)*255))
        ca=smooth((t-1.2)/0.7)
        if ca>0:
            s="Comment  EARS  — I'll send you the build"; tb=d.textbbox((0,0),s,font=f_cta)
            tw=tb[2]-tb[0]; bx0=960-tw/2-34; bx1=960+tw/2+34
            d.rounded_rectangle([bx0,650,bx1,726],radius=16,outline=CYAN+(int(ca*255),),width=3)
            d.text((960-tw/2,668),s,font=f_cta,fill=CYAN+(int(ca*255),))
        text_center(d,960,790,"built in public · donationware",f_sm,MUTED,int(smooth((t-1.6)/0.7)*255))
        base.convert("RGB").save(os.path.join(outdir,f"{i:05d}.png"))
    print(f"beat6: {nframes} frames")

# --- HRTF step 1: interaural time difference, wavefront to the ears ---------
def render_itd(outdir, nframes):
    os.makedirs(outdir, exist_ok=True)
    f_eye=font(FM,30); f_cap=font(FB,48); f_big=font(FB,38); f_sm=font(FM,24); f_rd=font(FM,30)
    cx, cy, R = 720, 560, 120
    Lx, Rx = cx-R, cx+R
    sx, sy = 1330, 300
    distR=math.hypot(sx-Rx, sy-cy); distL=math.hypot(sx-Lx, sy-cy)
    T=2.2; speed=430.0
    for i in range(nframes):
        t=i/FPS; base=BASE_BG.convert("RGBA").copy(); d=ImageDraw.Draw(base,"RGBA")
        intro=smooth(t/0.6); ha=int(intro*255)
        ea=int(smooth((t-0.3)/0.6)*255)
        d.text((120,90),"02",font=font(FM,34),fill=CYAN+(ea,)); d.text((188,92),"TIME DIFFERENCE",font=f_eye,fill=MUTED+(ea,))
        # path lines
        d.line([sx,sy,Rx,cy],fill=CYAN+(int(ha*0.9),),width=3)                 # near
        seg=16; px0=sx;
        # dashed far path
        n=int(math.hypot(sx-Lx,sy-cy)//seg)
        for k in range(0,n,2):
            u0=k/n; u1=min((k+1)/n,1.0)
            d.line([sx+(Lx-sx)*u0,sy+(cy-sy)*u0, sx+(Lx-sx)*u1,sy+(cy-sy)*u1],fill=MUTED+(int(ha*0.8),),width=3)
        # expanding wavefront rings centred on the source
        if t>0.8:
            cyc=(t-0.8)%T; r=cyc*speed
            for off,al in ((0,150),(70,70),(140,30)):
                rr=r-off
                if rr>8: d.ellipse([sx-rr,sy-rr,sx+rr,sy+rr],outline=CYAN+(int(al*intro),),width=3)
            flashR=max(0.0,1-abs(r-distR)/40); flashL=max(0.0,1-abs(r-distL)/40)
        else:
            flashR=flashL=0.0
        # head + ears, flash the ear as the wavefront passes
        d.ellipse([cx-R,cy-R,cx+R,cy+R],fill=PANEL+(ha,),outline=CYAN+(ha,),width=4)
        d.polygon([(cx,cy-R-20),(cx-15,cy-R+6),(cx+15,cy-R+6)],fill=CYAN+(ha,))
        for ex,lab,fl in ((Lx,"L",flashL),(Rx,"R",flashR)):
            if fl>0: glow_dot(base,ex,cy,26,CYAN,fl)
            d.ellipse([ex-12,cy-24,ex+12,cy+24],fill=CYAN_D+(ha,),outline=CYAN+(ha,),width=2)
        # source
        glow_dot(base,sx,sy,14,(140,240,255)); d.text((sx-30,sy-54),"sound",font=f_sm,fill=MUTED+(ha,))
        # extra-distance bracket on far path (near the L ear)
        d.text((Lx-40,cy+42),"farther = later",font=f_sm,fill=MUTED+(ha,))
        d.text((Rx-30,cy+42),"nearer = first",font=f_sm,fill=CYAN+(ha,))
        # readout
        rd_a=int(smooth((t-1.4)/0.8)*255) if t>1.4 else 0
        d.text((1120,560),"right ear first",font=f_rd,fill=CYAN+(rd_a,))
        d.text((1120,604),"left ear  ~0.6 ms later",font=f_rd,fill=MUTED+(rd_a,))
        text_center(d,960,196,"A sound reaches one ear a little sooner.",f_big,MUTED,int(smooth((t-0.4)/0.7)*255))
        lower_third(base,"Interaural time difference: your first clue to direction",f_cap,(t-1.0)/0.6)
        base.convert("RGB").save(os.path.join(outdir,f"{i:05d}.png"))
    print(f"itd: {nframes} frames")

RENDER={"b1":render_beat1,"b2":render_beat2,"b3":render_beat3,"b5":render_beat5,"b6":render_beat6,"itd":render_itd}
if __name__ == "__main__":
    if sys.argv[1]=="preview":
        beat=sys.argv[2]; tt=float(sys.argv[3]); outpng=sys.argv[4]; dur=float(sys.argv[5])
        import tempfile
        tmpd=tempfile.mkdtemp(); fi=round(tt*FPS)
        # render just enough frames up to tt (cheap for stateless beats; b1/b2 have trails so render from 0)
        RENDER[beat](tmpd, fi+1)
        Image.open(os.path.join(tmpd,f"{fi:05d}.png")).save(outpng)
        print("preview ->",outpng)
    else:
        beat=sys.argv[1]; outdir=sys.argv[2]; dur=float(sys.argv[3])
        RENDER[beat](outdir, round(dur*FPS))
