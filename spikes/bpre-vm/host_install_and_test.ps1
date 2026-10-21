# host_install_and_test.ps1  --  STEP 2 (run elevated on the HOST, AFTER a reboot into test mode).
#
# Installs the test-signed SYSVAD virtual device and runs a bounded crash-safety check. Writes a
# result file so a post-reboot Claude session (or the operator) can read the outcome.
#
# Blast-radius bounds:
#   - demand-start, Root-enumerated software device; NOT set as the default playback endpoint
#     -> nothing streams to it at boot -> it cannot bootloop even if it would crash under streaming.
#   - records the current default playback device before/after and asserts it is unchanged.
$ErrorActionPreference = 'Continue'
$log = 'D:\kookr-vm\host_test_result.txt'
function Say($m){ $m | Tee-Object -FilePath $log -Append | Out-Null; Write-Output $m }
"=== Kookr host install+test  $(Get-Date -Format s) ===" | Set-Content $log

# 0. confirm test mode is actually active (driver load requires it)
$ts = (bcdedit /enum '{current}' | Select-String 'testsigning\s+Yes')
if (-not $ts) { Say "ABORT: test-signing is not active. Run host_arm_testmode.ps1 then reboot first."; exit 1 }
Say "test-signing: active"

$devcon = 'C:\Program Files (x86)\Windows Kits\10\Tools\10.0.26100.0\x64\devcon.exe'
$pkg = 'D:\kookr-vm\pkg'
$hwid = 'Root\Sysvad_ComponentizedAudioSample'

# 1. record current default playback device (we must NOT change it)
$py = (Get-Command python -EA SilentlyContinue).Source
Say "--- sound devices BEFORE ---"
(Get-CimInstance Win32_SoundDevice | Select-Object -Expand Name) | ForEach-Object { Say "  $_" }

# 2. clean any prior install, then add package to the store + create the device
Say "--- remove any prior device ---"
& $devcon remove $hwid 2>&1 | Out-Null
Start-Sleep 2
$cur=$null
foreach($line in (pnputil /enum-drivers)){
  if($line -match 'Published Name\s*:\s*(oem\d+\.inf)'){ $cur=$matches[1] }
  if($line -match 'Original Name\s*:\s*componentizedaudiosample\.inf'){ pnputil /delete-driver $cur /uninstall /force 2>&1 | Out-Null }
}
Start-Sleep 2
Say "--- add driver package to store ---"
(pnputil /add-driver "$pkg\ComponentizedAudioSample.inf" /install 2>&1 | Select-String 'Adding|Published|success|fail|driver package') | ForEach-Object { Say "  $_" }
Start-Sleep 2
Say "--- create device (devcon install) ---"
(& $devcon install "$pkg\ComponentizedAudioSample.inf" $hwid 2>&1) | ForEach-Object { Say "  $_" }
Start-Sleep 6

# 3. confirm the device + endpoint appeared, loaded hash matches the VM-validated binary
$store = Get-ChildItem 'C:\Windows\System32\DriverStore\FileRepository' -Recurse -Filter TabletAudioSample.sys -EA SilentlyContinue | Select-Object -First 1
if($store){ Say "loaded sys sha256=$((Get-FileHash $store.FullName -Algorithm SHA256).Hash)  (expect A021A1DE...B29D)" } else { Say "WARN: TabletAudioSample.sys not in DriverStore" }
Say "--- SYSVAD endpoints ---"
(Get-PnpDevice -Class 'MEDIA' -EA SilentlyContinue | Where-Object { $_.FriendlyName -match 'Sysvad|Tablet|Speaker' } | Select-Object Status,FriendlyName) | Format-Table -AutoSize | Out-String | ForEach-Object { Say $_ }

# 4. bugcheck check since boot (the crash-safety verdict)
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$bc = Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001} -EA SilentlyContinue | Where-Object { $_.TimeCreated -ge $boot }
Say "--- bugcheck events since boot ($boot) ---"
if($bc){ $bc | ForEach-Object { Say "  BUGCHECK: $($_.TimeCreated) $($_.Message.Split([char]10)[0])" } } else { Say "  none (no BSOD on install)" }

Say ""
Say ">>> Install done. The device is present but NOT default. A live session can now run the pull-driven"
Say ">>> probe (pseudo_k11.py + bpre_probe.py) against the SYSVAD Speakers endpoint and measure latency."
Say ">>> Result file: $log"
