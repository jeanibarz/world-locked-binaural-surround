' Launch World-Locked Binaural Surround with no console window (only the app window shows).
' Points at the standalone Rust engine bundle (zero Python). Adjust the path if the bundle
' lives elsewhere; the engine resolves its own assets relative to its exe.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\ibarz\WLBS-Engine"
sh.Run """C:\Users\ibarz\WLBS-Engine\wlbs_engine.exe""", 0, False
