#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
V="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30"
WIN="/mnt/c/Users/ibarz/Videos"; PY=/home/jean/git/binaural-localization/.venv/bin/python
rm -rf fc_indiv fb_spec
$PY hrtf_anim.py
ffmpeg -nostdin -v error -y -framerate 30 -i fc_indiv/%05d.png $V seq4_individuality.mp4
cp -f seq4_individuality.mp4 "$WIN/world-locked-seq4_individuality.mp4" 2>/dev/null || true
printf "seq4 %ss\n" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 seq4_individuality.mp4)"
ffmpeg -nostdin -v error -y -ss 5 -i seq4_individuality.mp4 -frames:v 1 iv_a.png
ffmpeg -nostdin -v error -y -ss 8.8 -i seq4_individuality.mp4 -frames:v 1 iv_b.png
ffmpeg -nostdin -v error -y -i iv_a.png -i iv_b.png -filter_complex "[0]scale=900:-1[a];[1]scale=900:-1[b];[a][b]hstack" seq4_sheet.png
echo ALLDONE
