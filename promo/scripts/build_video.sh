#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
SRC="/mnt/c/Users/ibarz/Videos/2026-10-28 18-53-47.mkv"
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf

STARTS=(185 345 120 335 10 45)
CAPS=("Sound is stuck inside your head" \
      "Binaural: what each ear really hears" \
      "World-locked: the room stays still, you move" \
      "Your webcam -> head pose -> re-render" \
      "Rust engine - VB-Audio Cable - live EQ" \
      "Comment EARS to get the build")

: > concat.txt
for idx in 0 1 2 3 4 5; do
  i=$((idx+1))
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "vo$i.mp3")
  start=${STARTS[$idx]}
  esc=$(printf '%s' "${CAPS[$idx]}" | sed "s/:/\\\\:/g")
  echo "seg$i: start=$start dur=$dur"
  ffmpeg -nostdin -v error -y -ss "$start" -t "$dur" -i "$SRC" -i "vo$i.mp3" \
    -filter_complex "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,fps=30,drawtext=fontfile=$FONT:text='$esc':x=(w-text_w)/2:y=h-128:fontsize=46:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=20[v]" \
    -map "[v]" -map 1:a -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p \
    -c:a aac -b:a 160k -ac 2 -ar 48000 -shortest "seg$i.mp4"
  echo "file 'seg$i.mp4'" >> concat.txt
done

echo "=== concat ==="
ffmpeg -nostdin -v error -y -f concat -safe 0 -i concat.txt -c copy roughcut_raw.mp4
echo "=== loudnorm ==="
ffmpeg -nostdin -v error -y -i roughcut_raw.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=11" -c:a aac -b:a 160k world-locked-roughcut.mp4
cp -f world-locked-roughcut.mp4 "/mnt/c/Users/ibarz/Videos/world-locked-roughcut.mp4" 2>/dev/null && echo "copied to Windows Videos" || echo "WARN: windows copy failed"
echo "=== RESULT ==="
printf "duration: %ss\n" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 world-locked-roughcut.mp4)"
ls -la world-locked-roughcut.mp4
echo ALLDONE
