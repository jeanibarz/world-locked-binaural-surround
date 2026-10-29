#!/usr/bin/env bash
# Portable, reproducible full-explainer render. Operates entirely inside a BUILD dir.
# Usage: render_all.sh [BUILD_DIR]   (default: $PWD)
# Needs in BUILD: anim.py hrtf_anim.py hrtf_anim2.py vizchrome.py mkmask.py  and  vo_full/vo_*.mp3
# Fixed external inputs: the source capture + IRCAM SOFA (absolute, persistent).
set -euo pipefail
BUILD="${1:-$PWD}"; cd "$BUILD"; export PROMO_OUT="$BUILD"
SRC="/mnt/c/Users/ibarz/Videos/2026-10-28 18-53-47.mkv"
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
SYSPY=python3; BLPY=/home/jean/git/binaural-localization/.venv/bin/python
V="-an -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30"
ENC="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 160k -ar 48000 -ac 2"
dur(){ ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"; }
VF=vo_full

echo "[1/5] visuals: beats (Pillow)"
$SYSPY anim.py b1  f_b1  16
$SYSPY anim.py b2  f_b2  16
$SYSPY anim.py b3  f_b3  22
$SYSPY anim.py b5  f_b5  16
$SYSPY anim.py b6  f_b6  14
$SYSPY anim.py itd f_itd 11
for b in b1 b2 b3 b5 b6 itd; do ffmpeg -nostdin -v error -y -framerate 30 -i f_$b/%05d.png $V beatv_$b.mp4; done

echo "[2/5] visuals: data clips (matplotlib, real IRCAM data)"
$BLPY hrtf_anim.py        # fb_spec, fc_indiv
$BLPY hrtf_anim2.py       # fa2_itd, fe_cone
ffmpeg -nostdin -v error -y -framerate 30 -i fb_spec/%05d.png  $V seq3.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fc_indiv/%05d.png $V seq4.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fa2_itd/%05d.png  $V seq2b.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fe_cone/%05d.png  $V seq5.mp4

echo "[3/5] visuals: webcam punch-in (blur), cold, pivot, timing"
$SYSPY mkmask.py
P4="(clip(t/15\,0\,1)*clip(t/15\,0\,1)*(3-2*clip(t/15\,0\,1)))"
ffmpeg -nostdin -v error -y -ss 335 -t 15 -i "$SRC" -loop 1 -t 15 -i mask_face.png -filter_complex \
"[0:v]crop=1150:647:0:300,setsar=1,zoompan=z='min(1.5+1.0*on/449,2.5)':x='366-iw/zoom/2':y='186-ih/zoom/2':d=1:s=1920x1080:fps=30,split[s][b];\
[b]gblur=sigma=22,eq=brightness=-0.05:saturation=0.9[bb];[s]format=yuva420p[sf];[1:v]format=gray,scale=1920:1080[mk];\
[sf][mk]alphamerge[sa];[bb][sa]overlay=0:0,vignette=PI/4.5,\
drawtext=fontfile=${FONT}:text='Your webcam tracks your head, the sound follows':x=(w-text_w)/2:y=h-128:fontsize=44:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=20[v]" \
-map "[v]" $V webcamv.mp4
# cold: source money-shot crop
ffmpeg -nostdin -v error -y -ss 118 -t 16 -i "$SRC" -vf "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1" $V coldv.mp4
# pivot card
ffmpeg -nostdin -v error -y -f lavfi -t 8 -i color=c=0x0a0e14:s=1920x1080:r=30 \
 -vf "drawtext=fontfile=$FONT:text='First, how do you actually hear in 3D?':x=(w-text_w)/2:y=(h-text_h)/2-40:fontsize=60:fontcolor=0xe8eef0,drawtext=fontfile=$FONT:text='3 minutes. Do not move.':x=(w-text_w)/2:y=(h/2)+60:fontsize=38:fontcolor=0x8a9aa3" $V pivotv.mp4
# timing = wavefront + ITD sweep
printf "file 'beatv_itd.mp4'\nfile 'seq2b.mp4'\n" > tcat.txt
ffmpeg -nostdin -v error -y -f concat -safe 0 -i tcat.txt $V timingv.mp4

echo "[4/5] assemble: stretch each visual to its VO segment, mux VO"
# map: out_index  visual  vo_segment
map=(
"01 coldv.mp4      vo_01_cold"
"02 pivotv.mp4     vo_02_pivot"
"03 beatv_b1.mp4   vo_03_stereo"
"04 beatv_b2.mp4   vo_04_binaural"
"05 timingv.mp4    vo_05_timing"
"06 seq3.mp4       vo_06_colour"
"07 seq5.mp4       vo_07_cone"
"08 seq4.mp4       vo_08_individuality"
"09 beatv_b3.mp4   vo_09_missingcue"
"10 webcamv.mp4    vo_10_tracker"
"11 beatv_b5.mp4   vo_11_hood"
"12 beatv_b6.mp4   vo_12_cta"
)
: > acat.txt
for row in "${map[@]}"; do
  set -- $row; idx=$1; vis=$2; voseg=$3
  vd=$(dur "$VF/$voseg.mp3"); cd_=$(dur "$vis")
  fac=$($SYSPY -c "print(max(0.2,$vd/$cd_))")
  ffmpeg -nostdin -v error -y -i "$vis" -i "$VF/$voseg.mp3" \
    -filter_complex "[0:v]setpts=${fac}*PTS,fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1:color=0x0a0e14,setsar=1[v]" \
    -map "[v]" -map 1:a -shortest $ENC "a_$idx.mp4"
  echo "file 'a_$idx.mp4'" >> acat.txt
done

echo "[5/5] concat + loudnorm -> explainer.mp4"
ffmpeg -nostdin -v error -y -f concat -safe 0 -i acat.txt -c copy assembly_raw.mp4
ffmpeg -nostdin -v error -y -i assembly_raw.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 160k explainer.mp4
printf "DONE  explainer.mp4  %ss\n" "$(dur explainer.mp4)"
