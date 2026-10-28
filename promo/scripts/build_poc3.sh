#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
ENC="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 160k -ar 48000 -ac 2"
dur1=$(ffprobe -v error -show_entries format=duration -of csv=p=0 vo1.mp3)

echo "=== re-render beat1 (fixed visual) ==="
rm -rf f_b1; python3 anim.py b1 f_b1 "$dur1"

echo "=== stereo L<->R audio demo mixed under VO ==="
# pink-noise 'whoosh' panned L<->R (apulsator sine), delayed to match the dot (~0.8s), low under VO
ffmpeg -nostdin -v error -y -i vo1.mp3 -filter_complex \
"anoisesrc=color=pink:r=48000:a=0.6:d=${dur1},aformat=channel_layouts=stereo,highpass=f=360,lowpass=f=2500,\
apulsator=hz=0.223:mode=sine:amount=1,volume=0.18,adelay=800|800[bed];\
[0:a]aformat=channel_layouts=stereo[vo];\
[vo][bed]amix=inputs=2:duration=first:normalize=0[a]" \
-map "[a]" -c:a pcm_s16le vo1_mixed.wav

echo "=== seg_b1 (new frames + mixed audio) ==="
ffmpeg -nostdin -v error -y -framerate 30 -i f_b1/%05d.png -i vo1_mixed.wav $ENC -shortest seg_b1.mp4

echo "=== concat with existing seg_b4 ==="
printf "file 'seg_b1.mp4'\nfile 'seg_b4.mp4'\n" > poc_concat.txt
ffmpeg -nostdin -v error -y -f concat -safe 0 -i poc_concat.txt -c copy poc_raw.mp4

echo "=== loudnorm ==="
ffmpeg -nostdin -v error -y -i poc_raw.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 160k world-locked-poc.mp4
cp -f world-locked-poc.mp4 "/mnt/c/Users/ibarz/Videos/world-locked-poc.mp4" 2>/dev/null && echo "copied to Windows Videos" || echo "WARN windows copy failed"
echo "duration: $(ffprobe -v error -show_entries format=duration -of csv=p=0 world-locked-poc.mp4)s"
ffmpeg -nostdin -v error -y -ss 6 -i world-locked-poc.mp4 -frames:v 1 -vf scale=820:-1 poc_chk_b1b.jpg
echo ALLDONE
