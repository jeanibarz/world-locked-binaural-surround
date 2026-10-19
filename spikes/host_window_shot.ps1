# Screenshot a window on the Windows host by title, DPI-aware, into a PNG.
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w spikes/host_window_shot.ps1)" `
#       '*cockpit*' 'C:\Users\<user>\BinauralHeadtracker\shot.png' [width height]
#
# Why a script: the capturer MUST be DPI-aware too (our GUI processes are per-monitor-aware on a
# 150% display), otherwise GetWindowRect/CopyFromScreen are in virtualised 96-dpi coordinates and
# the sizes don't match the app's pixels. It also raises + resizes the window to a fixed size so
# successive shots are comparable pixel-for-pixel. Pipe through `grep -v UtilAcceptVsock` from WSL.
# NB: PowerShell variable names are case-insensitive — keep every name here distinct.
param(
    [string]$TitleLike = '*cockpit*',
    [string]$Out = 'C:\Users\ibarz\BinauralHeadtracker\shot.png',
    [int]$Width = 1400,
    [int]$Height = 880
)
Add-Type 'using System;using System.Runtime.InteropServices;public class Cap{[DllImport("user32.dll")]public static extern bool SetProcessDpiAwarenessContext(IntPtr c);[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int n);[DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int w,int ht,uint f);[DllImport("user32.dll")]public static extern bool GetWindowRect(IntPtr h,out R r);[StructLayout(LayoutKind.Sequential)]public struct R{public int L,T,Rt,B;}}'
[Cap]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null   # -4 = per-monitor-aware v2
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Start-Sleep -Seconds 3   # give a freshly launched app time to open its window
$proc = Get-Process | Where-Object { $_.MainWindowTitle -like $TitleLike } | Select-Object -First 1
if (-not $proc) { Write-Error "no window with title like $TitleLike"; exit 1 }
$hwnd = $proc.MainWindowHandle
[Cap]::ShowWindow($hwnd, 9) | Out-Null                                            # SW_RESTORE
[Cap]::SetWindowPos($hwnd, [IntPtr](-1), 100, 80, $Width, $Height, 0x40) | Out-Null  # topmost, SWP_SHOWWINDOW
Start-Sleep -Milliseconds 1500                                                    # let the app re-layout + redraw
$rect = New-Object Cap+R; [Cap]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($rect.Rt - $rect.L), ($rect.B - $rect.T)
$gfx = [System.Drawing.Graphics]::FromImage($bmp); $gfx.CopyFromScreen($rect.L, $rect.T, 0, 0, $bmp.Size)
$bmp.Save($Out); Write-Output ('OK ' + $bmp.Width + 'x' + $bmp.Height + ' ' + $Out)
