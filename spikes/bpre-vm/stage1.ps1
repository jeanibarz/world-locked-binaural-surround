# stage1.ps1 — autonomous host prep for Track B B-pre: install WDK/SDK + fetch Win11 ISO.
# Runs detached (scheduled task, SYSTEM). Logs to D:\kookr-vm\build_stage1.log.
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ws  = 'D:\kookr-vm'
$log = "$ws\build_stage1.log"
function Log($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8 }
Log "STAGE1 START"

# --- Win11 ISO via Fido (start async BITS first; it's the long pole) ---
try {
  $fido = "$ws\Fido.ps1"
  Invoke-WebRequest -Uri 'https://github.com/pbatard/Fido/raw/master/Fido.ps1' -OutFile $fido -UseBasicParsing
  Log "Fido downloaded"
  $out = & $fido -Win 11 -Lang English -Arch x64 -GetUrl 2>&1
  $url = ($out | Select-String '^https?://').ToString() | Select-Object -Last 1
  if (-not $url) { $url = ($out | Where-Object { $_ -match '^https?://' } | Select-Object -Last 1) }
  Log "ISO URL: $url"
  if ($url -match '^https?://') {
    Start-BitsTransfer -Source $url -Destination "$ws\win11.iso" -Asynchronous -DisplayName kookrISO | Out-Null
    Log "ISO BITS transfer started (async)"
  } else { Log "Fido returned no URL. Raw: $out" }
} catch { Log "Fido/ISO error: $_" }

# --- SDK 26100 (matches WDK 26100.6584 for VS2022) ---
try {
  Log "downloading SDK bootstrapper"
  Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/?linkid=2338977' -OutFile "$ws\winsdksetup.exe" -UseBasicParsing
  Log "installing SDK (silent; downloads payload)..."
  $p = Start-Process "$ws\winsdksetup.exe" -ArgumentList '/features','+','/quiet','/norestart','/ceip','off' -Wait -PassThru
  Log "SDK install exit code: $($p.ExitCode)"
} catch { Log "SDK error: $_" }

# --- WDK 26100.6584 ---
try {
  Log "downloading WDK bootstrapper"
  Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/?linkid=2335869' -OutFile "$ws\wdksetup.exe" -UseBasicParsing
  Log "installing WDK (silent; downloads payload)..."
  $p = Start-Process "$ws\wdksetup.exe" -ArgumentList '/features','+','/quiet','/norestart','/ceip','off' -Wait -PassThru
  Log "WDK install exit code: $($p.ExitCode)"
} catch { Log "WDK error: $_" }

# --- wait for the ISO ---
Log "waiting for ISO transfer..."
for ($i=0; $i -lt 400; $i++) {
  Start-Sleep 15
  $j = Get-BitsTransfer -Name kookrISO -ErrorAction SilentlyContinue
  if (-not $j) { Log "no BITS job found"; break }
  $gb = [math]::Round($j.BytesTransferred/1GB,2); $tot = [math]::Round($j.BytesTotal/1GB,2)
  if ($i % 4 -eq 0) { Log "ISO: $($j.JobState) $gb/$tot GB" }
  if ($j.JobState -eq 'Transferred') { Complete-BitsTransfer -BitsJob $j; Log "ISO complete"; break }
  if ($j.JobState -in 'Error','TransientError') { Log "ISO BITS state: $($j.JobState)"; }
}

# --- verify ---
$km = @(Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\Include\*\km\portcls.h' -ErrorAction SilentlyContinue)
Log ("WDK km/portcls.h present: " + ($km.Count -gt 0) + " " + ($km.FullName -join ';'))
if (Test-Path "$ws\win11.iso") { Log ("ISO size GB: " + [math]::Round((Get-Item "$ws\win11.iso").Length/1GB,2)) } else { Log "ISO missing" }
Log "STAGE1 DONE"
