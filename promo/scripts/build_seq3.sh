#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
V="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30"
WIN="/mnt/c/Users/ibarz/Videos"; PY=/home/jean/git/binaural-localization/.venv/bin/python
rm -rf fb_spec fc_indiv fa2_itd fe_cone
$PY hrtf_anim.py
$PY hrtf_anim2.py
ffmpeg -nostdin -v error -y -framerate 30 -i fb_spec/%05d.png  $V seq3_spectra_morph.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fc_indiv/%05d.png $V seq4_individuality.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fa2_itd/%05d.png  $V seq2b_itd_sweep.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fe_cone/%05d.png  $V seq5_cone_of_confusion.mp4
for f in seq2b_itd_sweep seq3_spectra_morph seq4_individuality seq5_cone_of_confusion; do
  cp -f "$f.mp4" "$WIN/world-locked-$f.mp4" 2>/dev/null || true
  printf "%-26s %ss\n" "$f" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 $f.mp4)"
done
ffmpeg -nostdin -v error -y -ss 2 -i seq2b_itd_sweep.mp4 -frames:v 1 r1.png
ffmpeg -nostdin -v error -y -ss 5 -i seq3_spectra_morph.mp4 -frames:v 1 r2.png
ffmpeg -nostdin -v error -y -ss 7 -i seq4_individuality.mp4 -frames:v 1 r3.png
ffmpeg -nostdin -v error -y -ss 4 -i seq5_cone_of_confusion.mp4 -frames:v 1 r4.png
ffmpeg -nostdin -v error -y -i r1.png -i r2.png -i r3.png -i r4.png -filter_complex "[0]scale=640:-1[a];[1]scale=640:-1[b];[2]scale=640:-1[c];[3]scale=640:-1[d];[a][b]hstack[t];[c][d]hstack[u];[t][u]vstack" seq3_sheet.png
echo ALLDONE
