# make_w10_vm.ps1 -- provision a Windows 10 22H2 Hyper-V VM (kookr-bpre-w10) beside the Win11 VM, to
# validate the Win10-ported SYSVAD driver end-to-end before the host. Remasters win10.iso with
# autounattend at root + no-prompt UEFI boot (like stage3), then creates a Gen2 VM with Secure Boot
# OFF / no TPM (Win10 needs neither, and SB-off lets test-signing work without the toggle dance).
$ErrorActionPreference='Continue'
$ws='D:\kookr-vm'; $vm='kookr-bpre-w10'; $o=@()
function Say($m){ $script:o += "$(Get-Date -Format HH:mm:ss) $m" }
"=== make_w10_vm $(Get-Date -Format s) ===" | Out-Null
# 0. require iso + autounattend
if(-not (Test-Path "$ws\win10.iso")){ Say "win10.iso MISSING"; Set-Content "$ws\make_w10.txt" ($o -join "`n") -Encoding UTF8; return }
if(-not (Test-Path "$ws\autounattend_w10.xml")){ Say "autounattend_w10.xml MISSING"; Set-Content "$ws\make_w10.txt" ($o -join "`n") -Encoding UTF8; return }
# 1. oscdimg (reuse the one ADK installed earlier)
$oscdimg = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10' -Recurse -Filter oscdimg.exe -EA SilentlyContinue | Select-Object -First 1 -Expand FullName
if(-not $oscdimg){ Say "oscdimg MISSING (ADK)"; Set-Content "$ws\make_w10.txt" ($o -join "`n") -Encoding UTF8; return }
Say "oscdimg: $oscdimg"
# 2. mount + copy + inject autounattend at root
$src="$ws\iso_src_w10"
Remove-Item $src -Recurse -Force -EA SilentlyContinue; New-Item -ItemType Directory $src | Out-Null
$mnt=Mount-DiskImage -ImagePath "$ws\win10.iso" -PassThru; Start-Sleep 2
$drive=($mnt|Get-Volume).DriveLetter
Say "win10.iso mounted ${drive}:"
Copy-Item "${drive}:\*" $src -Recurse -Force
Dismount-DiskImage -ImagePath "$ws\win10.iso" | Out-Null
Copy-Item "$ws\autounattend_w10.xml" "$src\autounattend.xml" -Force
Say "copied ISO + autounattend.xml to root"
# 3. remaster bootable UEFI ISO (no-prompt)
$boot="2#p0,e,b$src\boot\etfsboot.com#pEF,e,b$src\efi\microsoft\boot\efisys_noprompt.bin"
$out="$ws\win10_auto.iso"; Remove-Item $out -Force -EA SilentlyContinue
& $oscdimg -m -o -u2 -udfver102 "-bootdata:$boot" $src $out
Say "oscdimg exit $LASTEXITCODE; win10_auto.iso GB: $(if(Test-Path $out){[math]::Round((Get-Item $out).Length/1GB,2)}else{'MISSING'})"
if(-not (Test-Path $out)){ Set-Content "$ws\make_w10.txt" ($o -join "`n") -Encoding UTF8; return }
# 4. create the VM (Gen2, SecureBoot OFF, no TPM)
if(Get-VM -Name $vm -EA SilentlyContinue){ Stop-VM $vm -TurnOff -Force -EA SilentlyContinue; Remove-VM $vm -Force; Say "removed existing $vm" }
Remove-Item "$ws\vm_w10" -Recurse -Force -EA SilentlyContinue
New-VM -Name $vm -Generation 2 -MemoryStartupBytes 8GB -Path "$ws\vm_w10" -NewVHDPath "$ws\vm_w10\os.vhdx" -NewVHDSizeBytes 64GB | Out-Null
Set-VM $vm -AutomaticCheckpointsEnabled $false
Set-VMMemory $vm -DynamicMemoryEnabled $false
Set-VMProcessor $vm -Count 4
Add-VMDvdDrive $vm -Path $out
Connect-VMNetworkAdapter -VMName $vm -SwitchName 'Default Switch' -EA SilentlyContinue
$dvd=Get-VMDvdDrive $vm
Set-VMFirmware $vm -FirstBootDevice $dvd -EnableSecureBoot Off
Say "VM '$vm' created (Gen2, 8GB, 4 vCPU, 64GB, SecureBoot OFF, no TPM)"
Start-VM $vm
Say "VM started -- unattended Win10 install running"
Set-Content "$ws\make_w10.txt" ($o -join "`n") -Encoding UTF8
