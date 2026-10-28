# Getting started — install & first run (Windows)

This is the shortest path from "I have the app" to "I can hear world-locked
surround sound on my headphones", written for a first-time Windows user with **no
developer setup** — no WSL, no Python, no checkout of this repository. You run one
program and open one web page.

> **What you will end up with.** A small background program (`wlbs_engine.exe`)
> takes the surround sound your games, films, or music produce, and re-renders it
> for your headphones so the sound stays **fixed in the room while you turn your
> head** — the effect this project is built around (see the [README](../README.md)
> for the idea). You steer and monitor it from a web page called the **cockpit**
> that the program **serves on your own computer and you open in your browser**.

**Work through the sections below in order** (1 → 2 → 3 → 4). If you are a developer
who wants to run the code from a source checkout instead, this is **not** the page
you want — see [`../host/README.md`](../host/README.md) and the
[onboarding guides](#where-to-go-next) below.

---

## 1. What you need first (prerequisites)

Check these before you start.

| You need | Why | When it's needed |
|----------|-----|------------------|
| **Windows 10 or 11, 64-bit** | the engine is a native Windows program | to start the app |
| **Headphones** (any wired or wireless stereo pair) | the whole point is headphone listening | to hear anything |
| **A virtual 7.1 audio device** (e.g. VoiceMeeter) | this is how other apps' surround sound reaches the engine | to hear real sound |
| **A webcam** (built-in or USB) | it watches your head so the sound can stay world-locked | for the head-tracking effect |
| **A web browser** (Edge, Chrome, Firefox…) | the cockpit is a local web page | to see the cockpit |

**"7.1"** means a surround layout with 8 channels — front left/right/centre, the
two sides, the two rears, and a subwoofer. The virtual device pretends to be such a
speaker set so Windows apps will send their full surround mix to it.

**About the virtual 7.1 device.** Windows has no built-in way to hand one app's
surround audio to another app, so you install a free virtual audio device that acts
as a set of 7.1 speakers: your game or film player sends sound to it, and the
engine listens to it. Setting it up is a page of its own — do it once, following
[`virtual-device-setup.md`](virtual-device-setup.md) (VoiceMeeter is the
recommended, no-cost option there). The app will **start** without it, but it has
nothing to listen to and will stop almost immediately (it needs a live sound source
to run), so install the virtual device first.

You do **not** need to download any head-tracking model, impulse-response pack, or
other data file: everything the engine needs is already inside the app bundle
(details in [`prerequisite-assets.md`](prerequisite-assets.md)).

---

## 2. Get the app

The app ships as a **self-contained bundle** — a folder (usually delivered as a
`.zip`) that contains `wlbs_engine.exe` and the data files it needs, side by side:

```
wlbs_engine.exe     the program you run
DirectML.dll        a runtime file it needs (keep it next to the .exe)
assets\             the head-tracking models
packs\default\      the default surround→headphone sound profile
web_cockpit\        the cockpit web page
```

> **There is no public download yet.** A one-click installer / published build is
> still being worked on (tracked as issue #122). For now you get the bundle `.zip`
> **from whoever gave you the app** — the project owner or a build you were handed.
> When a public download exists, this section will link to it.

Once you have the `.zip`:

- **Unblock it** (Windows marks files from the internet as blocked): right-click
  the `.zip` → **Properties** → tick **Unblock** at the bottom → **OK**. Doing this
  on the zip *before* extracting saves you unblocking each file inside.
- **Extract** it to a folder you can find again, for example
  `C:\Users\<you>\WLBS\`. Keep **all** the files together — `wlbs_engine.exe` finds
  everything else by sitting next to it, so don't move the `.exe` out on its own.

That's the whole "install". There is nothing added to the Start menu and no admin
rights are required.

---

## 3. Launch it

### First, know which devices to point it at

The engine has to listen to **one** device (your virtual 7.1 device, from
Section 1) and play to **another** (your headphones). It can't guess your
headphones, so **you must tell it your headphone device** the first time.

Find your headphone name the way Windows shows it: right-click the **speaker icon**
in the taskbar → **Sound settings** → under **Output**, read the device name (for
example *"Headphones (FiiO USB DAC)"* or *"Speakers (Realtek Audio)"*). You only
need a **distinctive piece** of that name — something that won't also match another
device (prefer *"FiiO"* over *"USB"*, which might match several things).

### Launch with your devices — the recommended way

Make a one-line launcher so you don't retype anything. In the folder you extracted,
create a text file called **`run-engine.bat`** containing exactly this (if your
bundle already includes a `run-engine.bat`, just edit the two names inside it):

```bat
wlbs_engine.exe --source "VoiceMeeter" --output "<your headphones' name>"
```

- `--source "VoiceMeeter"` — the **virtual 7.1 device** your other apps play into
  (Section 1). Leave it as `VoiceMeeter` if that's what you installed.
- `--output "<your headphones' name>"` — replace the placeholder with the
  distinctive part of your headphone name you found above, e.g. `--output "FiiO"`.
  **Don't type `<your headphones' name>` literally.**

Then **double-click `run-engine.bat`**.

- The first time, Windows **SmartScreen** may show *"Windows protected your PC"*
  because the app is not yet signed. Click **More info → Run anyway**. (This is the
  normal prompt for any new unsigned program; it does not mean anything is wrong.)
- A small black **console window** opens and stays open — that is the engine
  running. Leave it open; closing it stops the engine. Among its first lines you
  will see one like:

  ```
  COCKPIT http://127.0.0.1:8732
  ```

> **Prefer to type the command yourself?** Open the extracted folder in **File
> Explorer**, click the **address bar**, type `cmd`, and press **Enter** — a
> Command Prompt opens already in that folder. Paste the `wlbs_engine.exe --source
> … --output …` line and press Enter. (Double-clicking `wlbs_engine.exe` on its own
> also works, but it uses a built-in default output device that is almost certainly
> not your headphones, so you'd hear nothing — hence the `--output` above.)

### Open the cockpit

In your web browser, go to **<http://localhost:8732>** (the same address the
console printed). The **cockpit** page loads: a round "radar" showing the ring of
virtual speakers with your head in the middle, a camera preview, and health
read-outs. This page is served only to your own computer — it is not on the
internet.

---

## 4. Did it work?

Go through this quick checklist. Each row tells you what you should see and what to
do if you don't.

| Check | You should see | If not |
|-------|----------------|--------|
| **Engine running** | the console window stays open (no error, no immediate close) | if it closes instantly, see [Troubleshooting](#troubleshooting) — usually no virtual sound source yet |
| **Cockpit loads** | <http://localhost:8732> shows the radar + panels, not a browser error | make sure the console still shows `COCKPIT http://127.0.0.1:8732`; use that exact address |
| **Camera sees you** | the camera preview shows your face, with tracking dots on it | allow camera access if Windows prompts; close other apps using the webcam |
| **Head tracking works** | **turn your head left and right** — the head icon on the radar turns with you, while the speaker dots stay put | if the head doesn't move, check the camera preview is live and your face is lit |
| **Sound is world-locked** | play 5.1/7.1 audio through your virtual device, put the headphones on, and turn your head — the sound **stays anchored in the room** instead of turning with you | if silent, confirm the virtual device (Section 1) and your `--output` headphone name (Section 3) |

If all five pass, you're done — the engine is doing exactly what it should.
Fine-tuning (which surround profile, correcting your headphones' tone) is optional
and covered in the [next steps](#where-to-go-next).

---

## Troubleshooting

**"Windows protected your PC" blocks the app.** That is SmartScreen reacting to an
unsigned program, not a virus warning about this app specifically. Click **More
info → Run anyway**. If you'd rather, unblock the `.exe` first: right-click it →
**Properties** → **Unblock** → **OK**.

**The console window flashes and disappears immediately.** The engine stops if it
can't open a sound source to listen to. Set up the virtual 7.1 device (Section 1 /
[`virtual-device-setup.md`](virtual-device-setup.md)) and make sure its name
matches your `--source`. To read the error message, launch from a Command Prompt
(see the tip in Section 3) so the window stays open and prints why it stopped.

**The cockpit page won't load / "can't reach this site".** Check the console window
is still open and still shows the `COCKPIT http://127.0.0.1:8732` line, and use that
exact address. If something else on your PC already uses port 8732, start the engine
on another port — add `--port 8777` to the launch line — and open
`http://localhost:8777` instead.

**No sound, or sound comes from the wrong device.** Without `--output`, the engine
uses a built-in default device name that is probably not yours. Relaunch with
`--output` set to a distinctive part of your headphones' name (Section 3). Also
confirm your game/film app is set to play into the **virtual 7.1 device**, not
straight to your headphones.

**The head doesn't follow me on the radar.** The engine needs to see your face.
Check the camera preview in the cockpit is live (not black), that no other app is
holding the webcam, and that your face is reasonably well lit and facing the camera.
(The engine still runs without a camera — you just get no head tracking.)

**A `HP_EQ_WARN … headphone EQ disabled` line in the console.** Harmless — it just
means no headphone EQ (tone-correction) profile is installed, so the engine renders
without it. Everything still works; see
[`headphone-compensation.md`](headphone-compensation.md) if you want to add one
later.

---

## Where to go next

Once it runs, these guides take it from "working" to "tuned for you":

- [`virtual-device-setup.md`](virtual-device-setup.md) — install and route the
  virtual Windows audio device (do this first if you skipped it).
- [`choosing-an-hrir-set.md`](choosing-an-hrir-set.md) — what the surround "sound
  profile" is and how to pick or load a different one in the cockpit.
- [`headphone-compensation.md`](headphone-compensation.md) — make the render sound
  neutral on *your* specific headphones.
- [`prerequisite-assets.md`](prerequisite-assets.md) — the full map of the data
  files the engine uses and where it looks for them (including how to add your own
  sound packs).
