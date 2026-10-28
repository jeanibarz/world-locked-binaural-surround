#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
SRC="/mnt/c/Users/ibarz/Videos/2026-10-28 18-53-47.mkv"
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
ENC="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 160k -ar 48000 -ac 2"

dur1=$(ffprobe -v error -show_entries format=duration -of csv=p=0 vo1.mp3)
dur4=$(ffprobe -v error -show_entries format=duration -of csv=p=0 vo4.mp3)

echo "=== render beat1 animation ($dur1 s) ==="
python3 anim.py b1 f_b1 "$dur1"

echo "=== seg_b1 (animation + vo1) ==="
ffmpeg -nostdin -v error -y -framerate 30 -i f_b1/%05d.png -i vo1.mp3 $ENC -shortest seg_b1.mp4

echo "=== seg_b4 (webcam punch-in + vo4) ==="
# pre-crop a working region around the webcam, then smooth zoom into the face mesh
P="(clip(t/${dur4}\,0\,1)*clip(t/${dur4}\,0\,1)*(3-2*clip(t/${dur4}\,0\,1)))"
CAP="Your webcam -> head pose -> re-render"
ffmpeg -nostdin -v error -y -ss 335 -t "$dur4" -i "$SRC" -i vo4.mp3 -filter_complex \
"[0:v]crop=1150:647:0:300,setsar=1,\
crop=w='1150-560*${P}':h='647-315*${P}':x='(575-209*${P})-(1150-560*${P})/2':y='(324-138*${P})-(647-315*${P})/2',\
scale=1920:1080,\
drawtext=fontfile=${FONT}:text='${CAP}':x=(w-text_w)/2:y=h-128:fontsize=46:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=20[v]" \
-map "[v]" -map 1:a $ENC -shortest seg_b4.mp4

echo "=== concat ==="
printf "file 'seg_b1.mp4'\nfile 'seg_b4.mp4'\n" > poc_concat.txt
ffmpeg -nostdin -v error -y -f concat -safe 0 -i poc_concat.txt -c copy poc_raw.mp4 \
  || { echo "copy-concat failed, re-encoding"; ffmpeg -nostdin -v error -y -f concat -safe 0 -i poc_concat.txt $ENC poc_raw.mp4; }

echo "=== loudnorm ==="
ffmpeg -nostdin -v error -y -i poc_raw.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 160k world-locked-poc.mp4
cp -f world-locked-poc.mp4 "/mnt/c/Users/ibarz/Videos/world-locked-poc.mp4" 2>/dev/null && echo "copied to Windows Videos" || echo "WARN windows copy failed"
echo "duration: $(ffprobe -v error -show_entries format=duration -of csv=p=0 world-locked-poc.mp4)s"
# preview frames
ffmpeg -nostdin -v error -y -ss 7 -i world-locked-poc.mp4 -frames:v 1 -vf scale=760:-1 poc_chk_b1.jpg
ffmpeg -nostdin -v error -y -ss 24 -i world-locked-poc.mp4 -frames:v 1 -vf scale=760:-1 poc_chk_b4.jpg
echo ALLDONE
