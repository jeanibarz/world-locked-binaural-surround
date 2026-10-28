import asyncio, os, time
from edge_tts import Communicate

VOICE = "en-US-AndrewNeural"
RATE = "-4%"

async def gen_one(i):
    out = f"vo{i}.mp3"
    if os.path.exists(out) and os.path.getsize(out) > 0:
        print(f"skip vo{i}.mp3 (exists)")
        return
    txt = open(f"b{i}.txt").read().strip()
    last = None
    for attempt in range(1, 6):
        try:
            tmp = out + ".part"
            if os.path.exists(tmp):
                os.remove(tmp)
            c = Communicate(txt, VOICE, rate=RATE)
            await c.save(tmp)
            if os.path.getsize(tmp) > 0:
                os.replace(tmp, out)
                print(f"wrote vo{i}.mp3 (attempt {attempt})")
                return
            raise RuntimeError("empty file")
        except Exception as e:
            last = e
            print(f"  vo{i} attempt {attempt} failed: {e!r}")
            await asyncio.sleep(2.5)
    raise SystemExit(f"vo{i}.mp3 failed after retries: {last!r}")

async def main():
    for i in range(1, 7):
        await gen_one(i)
        await asyncio.sleep(1.0)

asyncio.run(main())
