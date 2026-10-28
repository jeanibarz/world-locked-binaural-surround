#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
ENC="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 160k -ar 48000 -ac 2"
dur() { ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"; }
d2=$(dur vo2.mp3); d3=$(dur vo3.mp3); d5=$(dur vo5.mp3); d6=$(dur vo6.mp3)

echo "=== render animations (b2 $d2, b3 $d3, b5 $d5, b6 $d6) ==="
python3 anim.py b2 f_b2 "$d2"
python3 anim.py b3 f_b3 "$d3"
python3 anim.py b5 f_b5 "$d5"
python3 anim.py b6 f_b6 "$d6"

echo "=== beat2 orbit audio demo (L<->R pan around, under VO) ==="
PAN2="((cos(-1.5708+0.30*max(0\,t-1)))+1)*PI/4"
ffmpeg -nostdin -v error -y -i vo2.mp3 -filter_complex \
"anoisesrc=color=pink:r=48000:a=0.9:d=${d2},highpass=f=360,lowpass=f=2500,\
aeval=exprs='val(0)*cos(${PAN2})|val(0)*sin(${PAN2})':channel_layout=stereo,volume=0.20[bed];\
[0:a]aformat=channel_layouts=stereo[vo];[vo][bed]amix=inputs=2:duration=first:normalize=0[a]" \
-map "[a]" -c:a pcm_s16le vo2_mixed.wav

echo "=== build segments ==="
ffmpeg -nostdin -v error -y -framerate 30 -i f_b1/%05d.png -i vo1_mixed.wav $ENC -shortest seg_b1.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i f_b2/%05d.png -i vo2_mixed.wav $ENC -shortest seg_b2.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i f_b3/%05d.png -i vo3.mp3 $ENC -shortest seg_b3.mp4
# seg_b4.mp4 already built (blurred webcam punch-in)
ffmpeg -nostdin -v error -y -framerate 30 -i f_b5/%05d.png -i vo5.mp3 $ENC -shortest seg_b5.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i f_b6/%05d.png -i vo6.mp3 $ENC -shortest seg_b6.mp4

echo "=== concat 1..6 ==="
printf "file 'seg_b1.mp4'\nfile 'seg_b2.mp4'\nfile 'seg_b3.mp4'\nfile 'seg_b4.mp4'\nfile 'seg_b5.mp4'\nfile 'seg_b6.mp4'\n" > full_concat.txt
ffmpeg -nostdin -v error -y -f concat -safe 0 -i full_concat.txt -c copy full_raw.mp4
ffmpeg -nostdin -v error -y -i full_raw.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 160k world-locked-cut2.mp4
cp -f world-locked-cut2.mp4 "/mnt/c/Users/ibarz/Videos/world-locked-cut2.mp4" 2>/dev/null && echo "copied to Windows Videos" || echo "WARN windows copy failed"
echo "duration: $(dur world-locked-cut2.mp4)s"
# contact sheet of one frame per beat for review
: > /dev/null
echo ALLDONE
