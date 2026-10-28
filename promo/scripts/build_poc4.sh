#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
SRC="/mnt/c/Users/ibarz/Videos/2026-10-28 18-53-47.mkv"
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
ENC="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 160k -ar 48000 -ac 2"
dur1=$(ffprobe -v error -show_entries format=duration -of csv=p=0 vo1.mp3)
dur4=$(ffprobe -v error -show_entries format=duration -of csv=p=0 vo4.mp3)

echo "=== beat1 audio: deterministic L<->R pan locked to the dot ==="
PAN="(if(lt(t\,0.8)\,0\,sin(1.4*(t-0.8)))+1)*PI/4"
ffmpeg -nostdin -v error -y -i vo1.mp3 -filter_complex \
"anoisesrc=color=pink:r=48000:a=0.9:d=${dur1},highpass=f=360,lowpass=f=2500,\
aeval=exprs='val(0)*cos(${PAN})|val(0)*sin(${PAN})':channel_layout=stereo,volume=0.22[bed];\
[0:a]aformat=channel_layouts=stereo[vo];\
[vo][bed]amix=inputs=2:duration=first:normalize=0[a]" \
-map "[a]" -c:a pcm_s16le vo1_mixed.wav

echo "=== seg_b1 (frames unchanged + corrected audio) ==="
ffmpeg -nostdin -v error -y -framerate 30 -i f_b1/%05d.png -i vo1_mixed.wav $ENC -shortest seg_b1.mp4

echo "=== mask + seg_b4 (tighter punch-in, background blur, vignette) ==="
python3 mkmask.py
NF=$(python3 -c "print(round($dur4*30)-1)")
CAP="Your webcam -> head pose -> re-render"
ffmpeg -nostdin -v error -y -ss 335 -t "$dur4" -i "$SRC" -loop 1 -t "$dur4" -i mask_face.png -i vo4.mp3 -filter_complex \
"[0:v]crop=1150:647:0:300,setsar=1,\
zoompan=z='min(1.5+1.0*on/${NF},2.5)':x='366-iw/zoom/2':y='186-ih/zoom/2':d=1:s=1920x1080:fps=30,split[s][b];\
[b]gblur=sigma=22,eq=brightness=-0.05:saturation=0.9[bb];\
[s]format=yuva420p[sf];\
[1:v]format=gray,scale=1920:1080[mk];\
[sf][mk]alphamerge[sa];\
[bb][sa]overlay=0:0,vignette=PI/4.5,\
drawtext=fontfile=${FONT}:text='${CAP}':x=(w-text_w)/2:y=h-128:fontsize=46:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=20[v]" \
-map "[v]" -map 2:a $ENC -shortest seg_b4.mp4

echo "=== concat + loudnorm ==="
printf "file 'seg_b1.mp4'\nfile 'seg_b4.mp4'\n" > poc_concat.txt
ffmpeg -nostdin -v error -y -f concat -safe 0 -i poc_concat.txt -c copy poc_raw.mp4
ffmpeg -nostdin -v error -y -i poc_raw.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 160k world-locked-poc.mp4
cp -f world-locked-poc.mp4 "/mnt/c/Users/ibarz/Videos/world-locked-poc.mp4" 2>/dev/null && echo "copied to Windows Videos" || echo "WARN windows copy failed"
echo "duration: $(ffprobe -v error -show_entries format=duration -of csv=p=0 world-locked-poc.mp4)s"
ffmpeg -nostdin -v error -y -ss 25 -i world-locked-poc.mp4 -frames:v 1 -vf scale=820:-1 poc_chk_b4b.jpg
echo ALLDONE
