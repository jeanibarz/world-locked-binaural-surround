# Explainer video — autonomous improvement log

One entry per version. Each version = one hourly Kookr iteration: analyse the current
video, get fresh critic feedback (distinct lenses each time), apply many improvements
(keeping the fixed VO), re-render, save `world-locked-explainer-vNN.mp4` to the operator's
Videos folder, and make a local commit + tag `explainer-vNN`.

| version | tag | what changed |
|---|---|---|
| v01 | explainer-v01 | typography hierarchy fix (headlines to ink), unified caption scale + stable lower-third geometry, branded animated pivot card, dip-to-bg transitions between all 12 beats with black top/tail, restyled webcam caption |
| v02 | explainer-v02 | sync visuals to the words + kill mid-section drag: reordered missing-cue beat to match VO (parallax -> headphone-fail -> world-lock) with a decisive head-turn on the money line; phrase-keyed ITD sweep with front/back zero-marker pulse; HRTF label reveal on 'your HRTF'; cone camera-turn + 'movement breaks the tie' payoff; cold-open hook caption + slow push-in; removed dead air at beat openings; smoother HRTF/cone frames |
| v03 | explainer-v03 | data-viz rigour + 3D hero: fixed an honesty bug (individuality now level-aligns each subject so inter-ear spread visibly widens with frequency, matching the VO instead of contradicting it); tone-colour replotted as directional transfer fn vs the all-directions average (honest 0 dB baseline) with relocated direction inset; ITD plot gained a proper labelled ms y-axis (peak now on the tick ladder) + full +/-180 domain; cone of confusion gained front/up/nose anchors and a MEASURED front/back ITD readout that flips from a tie to tie-broken as the head turns; rebuilt the binaural beat as a true tilted-3D orbit on a constant-radius direction shell (source rises overhead on 'even above', depth-cued size/brightness, VU meters even out above); cinematic depth-push transition on all 12 cuts |
