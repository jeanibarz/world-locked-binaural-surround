"""Set every audio endpoint to full volume + unmuted (pycaw), so audiodg can't be rendering silence
because a SYSVAD render endpoint is muted/at zero in the VM."""
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL, cast, POINTER

n = 0
for dev in AudioUtilities.GetAllDevices():
    name = getattr(dev, "FriendlyName", "?")
    try:
        iface = dev._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(iface, POINTER(IAudioEndpointVolume))
        try:
            vol.SetMute(0, None)
        except Exception:  # noqa: BLE001
            pass
        vol.SetMasterVolumeLevelScalar(1.0, None)
        cur = vol.GetMasterVolumeLevelScalar()
        mute = vol.GetMute()
        print(f"set {name!r}: vol={cur:.2f} mute={mute}")
        n += 1
    except Exception as e:  # noqa: BLE001
        print(f"skip {name!r}: {repr(e)[:70]}")
print("endpoints set:", n)
