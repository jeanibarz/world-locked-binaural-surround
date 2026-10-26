import sys, time
from types import SimpleNamespace
apps_dir, src_dir = sys.argv[1], sys.argv[2]
sys.path[:0] = [apps_dir, src_dir]
import numpy as np, cv2, _console as C
from binaural_headtracker.diagnostics import classify
frame = np.full((720,960,3), 40, np.uint8)
ch=["FL","FR","FC","LFE","SL","SR","RL","RR"]; waz=[30.,-30.,0.,0.,110.,-110.,150.,-150.]
pose=SimpleNamespace(yaw=12.3,pitch=-4.1,roll=1.8)
diag=classify(block_size=512,samplerate=48000,render_ms=6.1,render_ms_peak=8.9,fifo_fill_blocks=2.,cushion_blocks=3,pose_rate_hz=60.,detect_ms=3.1,camera_fps=30.,cam_w=960,cam_h=720,render_label="rust")
kw=dict(title="t",live="TRACKING",pose=pose,world_az=waz,channels=ch,facing_i=2,head_deg=12.3,readout=[("rate","60Hz",C.GOOD)],buttons=[("Recenter","C",True),("Stats","S",False),("Quit","Q",False)],diagnosis=diag,show_stats=True,audio_stats=True)
for _ in range(5): C.compose(frame.copy(), **kw)
N=60; t=time.perf_counter()
for _ in range(N): C.compose(frame.copy(), **kw)
dt=(time.perf_counter()-t)/N*1000
print(f"compose (stats ON): {dt:.2f} ms/frame  ttf={getattr(C,'_type',None) and C._type.available()}")
