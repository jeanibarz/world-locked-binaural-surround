#!/usr/bin/env bash
set -euo pipefail
cd /tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/97432dac-b43b-491a-a91a-02b24812f7e8/scratchpad/cut
V="-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r 30"
WIN="/mnt/c/Users/ibarz/Videos"; PY=/home/jean/git/binaural-localization/.venv/bin/python
dur(){ ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"; }
echo "=== beats (Pillow) ==="
python3 anim.py b1 f_b1 "$(dur vo1.mp3)"
python3 anim.py b2 f_b2 "$(dur vo2.mp3)"
python3 anim.py b3 f_b3 "$(dur vo3.mp3)"
python3 anim.py b5 f_b5 "$(dur vo5.mp3)"
python3 anim.py b6 f_b6 "$(dur vo6.mp3)"
echo "=== data clips (matplotlib) ==="
$PY hrtf_anim.py
$PY hrtf_anim2.py
echo "=== encode data clips ==="
ffmpeg -nostdin -v error -y -framerate 30 -i fb_spec/%05d.png  $V seq3_spectra_morph.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fc_indiv/%05d.png $V seq4_individuality.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fa2_itd/%05d.png  $V seq2b_itd_sweep.mp4
ffmpeg -nostdin -v error -y -framerate 30 -i fe_cone/%05d.png  $V seq5_cone_of_confusion.mp4
for f in seq2b_itd_sweep seq3_spectra_morph seq4_individuality seq5_cone_of_confusion; do
  cp -f "$f.mp4" "$WIN/world-locked-$f.mp4" 2>/dev/null || true; done
echo "=== master sheet ==="
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
lblpng(){ ffmpeg -nostdin -v error -y -i "$1" -vf "scale=480:-1,drawtext=fontfile=$FONT:text='$2':x=6:y=6:fontsize=20:fontcolor=yellow:box=1:boxcolor=black@0.7" "ms_$3.png"; }
lblmp4(){ ffmpeg -nostdin -v error -y -ss "$2" -i "$1" -frames:v 1 -vf "scale=480:-1,drawtext=fontfile=$FONT:text='$3':x=6:y=6:fontsize=20:fontcolor=yellow:box=1:boxcolor=black@0.7" "ms_$4.png"; }
cp f_b1/00150.png t1.png; cp f_b2/00240.png t2.png; cp f_b3/00500.png t3.png; cp f_b5/00200.png t5.png; cp f_b6/00120.png t6.png
lblpng t1.png "b1 stereo" 01; lblpng t2.png "b2 binaural" 02; lblpng t3.png "b3 parallax" 03
lblmp4 world-locked-cut2.mp4 90 "b4 webcam" 04
lblpng t5.png "b5 engine" 05; lblpng t6.png "b6 endcard" 06
lblmp4 seq1_itd_wavefront.mp4 7 "s1 wavefront" 07
lblmp4 seq2b_itd_sweep.mp4 6 "s2 ITD sweep" 08
lblmp4 seq3_spectra_morph.mp4 5 "s3 spectra" 09
lblmp4 seq4_individuality.mp4 8.5 "s4 individ." 10
lblmp4 seq5_cone_of_confusion.mp4 5 "s5 cone" 11
ffmpeg -nostdin -v error -y -f lavfi -i color=c=0x0a0e14:s=480x270 -frames:v 1 ms_12.png
ffmpeg -nostdin -v error -y -i ms_01.png -i ms_02.png -i ms_03.png -i ms_04.png -i ms_05.png -i ms_06.png -i ms_07.png -i ms_08.png -i ms_09.png -i ms_10.png -i ms_11.png -i ms_12.png \
 -filter_complex "[0][1][2][3]hstack=4[r1];[4][5][6][7]hstack=4[r2];[8][9][10][11]hstack=4[r3];[r1][r2][r3]vstack=3" master_sheet2.png
echo ALLDONE
