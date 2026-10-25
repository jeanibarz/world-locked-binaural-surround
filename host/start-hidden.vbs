' Launch World-Locked Binaural Surround with no console window (only the app window shows).
Set sh = CreateObject("WScript.Shell")
sh.Environment("PROCESS")("WLBS_HIDDEN") = "1"
sh.CurrentDirectory = "C:\Users\ibarz\BinauralHeadtracker"
sh.Run "cmd /c """"C:\Users\ibarz\BinauralHeadtracker\run.cmd"""" > ""%TEMP%\wlbs.log"" 2>&1", 0, False
