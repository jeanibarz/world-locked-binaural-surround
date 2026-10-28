#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
V="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30"
WIN="/mnt/c/Users/ibarz/Videos"

echo "=== 1/4 ITD wavefront (Pillow) ==="
rm -rf f_itd; python3 anim.py itd f_itd 11
ffmpeg -nostdin -v error -y -framerate 30 -i f_itd/%05d.png $V seq1_itd_wavefront.mp4

echo "=== matplotlib real-data frames ==="
/home/jean/git/binaural-localization/.venv/bin/python hrtf_anim.py

echo "=== 2/4 ITD curve ==="
ffmpeg -nostdin -v error -y -framerate 30 -i fa_itd/%05d.png $V seq2_itd_curve.mp4
echo "=== 3/4 spectra morph ==="
ffmpeg -nostdin -v error -y -framerate 30 -i fb_spec/%05d.png $V seq3_spectra_morph.mp4
echo "=== 4/4 individuality ==="
ffmpeg -nostdin -v error -y -framerate 30 -i fc_indiv/%05d.png $V seq4_individuality.mp4

echo "=== copy to Windows + previews ==="
for f in seq1_itd_wavefront seq2_itd_curve seq3_spectra_morph seq4_individuality; do
  cp -f "$f.mp4" "$WIN/world-locked-$f.mp4" 2>/dev/null || true
  printf "%-26s %ss\n" "$f" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 $f.mp4)"
done
# contact sheet: a late frame from each
ffmpeg -nostdin -v error -y -ss 7 -i seq1_itd_wavefront.mp4 -frames:v 1 s1.png
ffmpeg -nostdin -v error -y -ss 5 -i seq2_itd_curve.mp4 -frames:v 1 s2.png
ffmpeg -nostdin -v error -y -ss 4 -i seq3_spectra_morph.mp4 -frames:v 1 s3.png
ffmpeg -nostdin -v error -y -ss 7 -i seq4_individuality.mp4 -frames:v 1 s4.png
ffmpeg -nostdin -v error -y -i s1.png -i s2.png -i s3.png -i s4.png -filter_complex "[0]scale=640:-1[a];[1]scale=640:-1[b];[2]scale=640:-1[c];[3]scale=640:-1[d];[a][b]hstack[t];[c][d]hstack[u];[t][u]vstack" seq_sheet.png
echo ALLDONE
