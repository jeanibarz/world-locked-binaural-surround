# stage3_remaster.ps1 — install ADK oscdimg, then remaster win11.iso with autounattend.xml at the
# root + a no-prompt UEFI bootloader. Output: D:\kookr-vm\win11_auto.iso. Logs to build_stage3.log.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ws = 'D:\kookr-vm'; $log = "$ws\build_stage3.log"
function Log($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8 }
Log "STAGE3 START"

# 1) oscdimg (ADK Deployment Tools)
$oscdimg = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10' -Recurse -Filter oscdimg.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $oscdimg) {
  Log "downloading ADK bootstrapper"
  Invoke-WebRequest 'https://go.microsoft.com/fwlink/?linkid=2289980' -OutFile "$ws\adksetup.exe" -UseBasicParsing
  Log "installing ADK Deployment Tools (silent)..."
  Start-Process "$ws\adksetup.exe" -ArgumentList '/features','OptionId.DeploymentTools','/quiet','/norestart','/ceip','off' -Wait
  for ($i=0; $i -lt 40; $i++) {
    Start-Sleep 15
    $oscdimg = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10' -Recurse -Filter oscdimg.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
    if ($oscdimg) { break }
  }
}
if (-not $oscdimg) { Log "oscdimg STILL MISSING - abort"; exit 1 }
Log "oscdimg: $oscdimg"

# 2) mount ISO, copy contents, inject autounattend.xml at the root
$src = "$ws\iso_src"
Remove-Item $src -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $src | Out-Null
$mnt = Mount-DiskImage -ImagePath "$ws\win11.iso" -PassThru
Start-Sleep 2
$drive = ($mnt | Get-Volume).DriveLetter
Log "ISO mounted at ${drive}:"
Copy-Item "${drive}:\*" $src -Recurse -Force
Dismount-DiskImage -ImagePath "$ws\win11.iso" | Out-Null
Copy-Item "$ws\autounattend.xml" "$src\autounattend.xml" -Force
Log "copied ISO + autounattend.xml to root"

# 3) remaster a bootable UEFI ISO with the no-prompt boot image (no 'press any key')
# paths have no spaces so no inner quoting needed
$boot = "2#p0,e,b$src\boot\etfsboot.com#pEF,e,b$src\efi\microsoft\boot\efisys_noprompt.bin"
$out = "$ws\win11_auto.iso"
Remove-Item $out -Force -ErrorAction SilentlyContinue
& $oscdimg -m -o -u2 -udfver102 "-bootdata:$boot" $src $out
Log "oscdimg exit $LASTEXITCODE"
if (Test-Path $out) { Log "win11_auto.iso size GB: $([math]::Round((Get-Item $out).Length/1GB,2))" } else { Log "win11_auto.iso MISSING" }
Log "STAGE3 DONE"
