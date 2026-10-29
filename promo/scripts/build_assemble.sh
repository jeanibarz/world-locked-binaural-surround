#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
SRC="/mnt/c/Users/ibarz/Videos/2026-10-28 18-53-47.mkv"
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
VF=vo_full
ENC="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 160k -ar 48000 -ac 2"
VENC="-an -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30"

echo "=== encode beat frame dirs to silent mp4 ==="
for b in b1 b2 b3 b5 b6; do
  ffmpeg -nostdin -v error -y -framerate 30 -i f_$b/%05d.png $VENC beatv_$b.mp4
done

echo "=== cold visual (source money shot, cropped 1080) ==="
ffmpeg -nostdin -v error -y -ss 118 -t 22 -i "$SRC" -vf "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1" $VENC coldv.mp4

echo "=== pivot card ==="
ffmpeg -nostdin -v error -y -f lavfi -t 9 -i color=c=0x0a0e14:s=1920x1080:r=30 \
 -vf "drawtext=fontfile=$FONT:text='First: how do you actually hear in 3D?':x=(w-text_w)/2:y=(h-text_h)/2-40:fontsize=60:fontcolor=0xe8eef0,drawtext=fontfile=$FONT:text='3 minutes. Do not move.':x=(w-text_w)/2:y=(h/2)+60:fontsize=38:fontcolor=0x8a9aa3" \
 $VENC pivotv.mp4

echo "=== timing visual = wavefront + ITD sweep ==="
printf "file 'seq1_itd_wavefront.mp4'\nfile 'seq2b_itd_sweep.mp4'\n" > tcat.txt
ffmpeg -nostdin -v error -y -f concat -safe 0 -i tcat.txt -an -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30 timingv.mp4

mkseg(){ ffmpeg -nostdin -v error -y -stream_loop -1 -i "$1" -i "$2" -map 0:v -map 1:a -shortest $ENC "$3"; }
echo "=== build 12 segments (visual looped to VO length + VO audio) ==="
mkseg coldv.mp4                  $VF/vo_01_cold.mp3          a_01.mp4
mkseg pivotv.mp4                 $VF/vo_02_pivot.mp3         a_02.mp4
mkseg beatv_b1.mp4               $VF/vo_03_stereo.mp3        a_03.mp4
mkseg beatv_b2.mp4               $VF/vo_04_binaural.mp3      a_04.mp4
mkseg timingv.mp4               $VF/vo_05_timing.mp3        a_05.mp4
mkseg seq3_spectra_morph.mp4     $VF/vo_06_colour.mp3        a_06.mp4
mkseg seq5_cone_of_confusion.mp4 $VF/vo_07_cone.mp3          a_07.mp4
mkseg seq4_individuality.mp4     $VF/vo_08_individuality.mp3 a_08.mp4
mkseg beatv_b3.mp4               $VF/vo_09_missingcue.mp3    a_09.mp4
mkseg seg_b4.mp4                 $VF/vo_10_tracker.mp3       a_10.mp4
mkseg beatv_b5.mp4               $VF/vo_11_hood.mp3          a_11.mp4
mkseg beatv_b6.mp4               $VF/vo_12_cta.mp3           a_12.mp4

echo "=== concat + loudnorm ==="
for i in $(seq -w 1 12); do echo "file 'a_$i.mp4'"; done > acat.txt
ffmpeg -nostdin -v error -y -f concat -safe 0 -i acat.txt -c copy assembly_raw.mp4
ffmpeg -nostdin -v error -y -i assembly_raw.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 160k world-locked-explainer-rough.mp4
cp -f world-locked-explainer-rough.mp4 "/mnt/c/Users/ibarz/Videos/world-locked-explainer-rough.mp4" 2>/dev/null && echo "copied to Windows"
echo "duration: $(ffprobe -v error -show_entries format=duration -of csv=p=0 world-locked-explainer-rough.mp4)s"
echo ALLDONE
