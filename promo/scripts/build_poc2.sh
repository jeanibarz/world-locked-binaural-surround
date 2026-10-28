#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
SRC="/mnt/c/Users/ibarz/Videos/2026-10-28 18-53-47.mkv"
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
ENC="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 160k -ar 48000 -ac 2"

dur4=$(ffprobe -v error -show_entries format=duration -of csv=p=0 vo4.mp3)
NF=$(python3 -c "print(round($dur4*30)-1)")

echo "=== seg_b1 (reuse rendered frames + vo1) ==="
ffmpeg -nostdin -v error -y -framerate 30 -i f_b1/%05d.png -i vo1.mp3 $ENC -shortest seg_b1.mp4

echo "=== seg_b4 (webcam punch-in via zoompan + vo4), frames=$NF ==="
CAP="Your webcam -> head pose -> re-render"
ffmpeg -nostdin -v error -y -ss 335 -t "$dur4" -i "$SRC" -i vo4.mp3 -filter_complex \
"[0:v]crop=1150:647:0:300,setsar=1,\
zoompan=z='min(1+0.95*on/${NF},1.95)':x='366-iw/zoom/2':y='186-ih/zoom/2':d=1:s=1920x1080:fps=30,\
drawtext=fontfile=${FONT}:text='${CAP}':x=(w-text_w)/2:y=h-128:fontsize=46:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=20[v]" \
-map "[v]" -map 1:a $ENC -shortest seg_b4.mp4

echo "=== concat ==="
printf "file 'seg_b1.mp4'\nfile 'seg_b4.mp4'\n" > poc_concat.txt
ffmpeg -nostdin -v error -y -f concat -safe 0 -i poc_concat.txt -c copy poc_raw.mp4

echo "=== loudnorm ==="
ffmpeg -nostdin -v error -y -i poc_raw.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 160k world-locked-poc.mp4
cp -f world-locked-poc.mp4 "/mnt/c/Users/ibarz/Videos/world-locked-poc.mp4" 2>/dev/null && echo "copied to Windows Videos" || echo "WARN windows copy failed"
echo "duration: $(ffprobe -v error -show_entries format=duration -of csv=p=0 world-locked-poc.mp4)s"
ffmpeg -nostdin -v error -y -ss 7  -i world-locked-poc.mp4 -frames:v 1 -vf scale=760:-1 poc_chk_b1.jpg
ffmpeg -nostdin -v error -y -ss 25 -i world-locked-poc.mp4 -frames:v 1 -vf scale=760:-1 poc_chk_b4.jpg
echo ALLDONE
