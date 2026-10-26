' Default launcher (Windows): start the NATIVE WINDOW (the Tauri shell).
'
' The shell starts the engine itself (hidden, on the fixed port 8731, --no-browser) and shows one
' native OS window. The cockpit HTTP server stays on http://127.0.0.1:8731 the whole time, so you can
' ALSO open that URL in any browser to inspect/debug the SAME live session, simultaneously with the
' window. Closing the window stops the app.
'
' Override the shell path with the WLBS_SHELL_EXE env var; the default is build-tauri.sh's output.
' (For the old console/Chrome path, run host\run.cmd directly instead.)
Set sh = CreateObject("WScript.Shell")
exe = sh.ExpandEnvironmentStrings("%WLBS_SHELL_EXE%")
If exe = "" Or exe = "%WLBS_SHELL_EXE%" Then
  exe = "C:\Users\ibarz\wlbs-build\src-tauri\target\release\wlbs-shell.exe"
End If
sh.Run """" & exe & """", 1, False
