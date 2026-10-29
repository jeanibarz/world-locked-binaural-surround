import asyncio, os, glob
from edge_tts import Communicate
VOICE="en-US-AndrewNeural"; RATE="-4%"
async def one(f):
    out=f[:-4]+".mp3"
    if os.path.exists(out) and os.path.getsize(out)>0:
        print("skip",out); return
    txt=open(f).read().strip()
    for att in range(1,6):
        try:
            tmp=out+".part"
            await Communicate(txt,VOICE,rate=RATE).save(tmp)
            if os.path.getsize(tmp)>0: os.replace(tmp,out); print("wrote",out,f"(try {att})"); return
            raise RuntimeError("empty")
        except Exception as e:
            print(" ",out,"attempt",att,"failed:",repr(e)); await asyncio.sleep(2.5)
    raise SystemExit("failed "+out)
async def main():
    for f in sorted(glob.glob("vo_*.txt")):
        await one(f); await asyncio.sleep(0.8)
asyncio.run(main())
