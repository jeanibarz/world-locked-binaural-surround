# stage2_vm.ps1 — create the Gen2 Hyper-V VM (vTPM + Secure Boot) and start the unattended Win11
# install. Run elevated (over the SSH channel). Logs to D:\kookr-vm\build_stage2.log.
$ErrorActionPreference = 'Stop'
$ws = 'D:\kookr-vm'; $vm = 'kookr-bpre'
$log = "$ws\build_stage2.log"
function Log($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8; Write-Output $m }
Log "STAGE2 START"

# clean any prior attempt
if (Get-VM -Name $vm -ErrorAction SilentlyContinue) {
  Stop-VM $vm -TurnOff -Force -ErrorAction SilentlyContinue
  Remove-VM $vm -Force
  Log "removed existing VM"
}
Remove-Item "$ws\os.vhdx","$ws\unattend.vhdx" -Force -ErrorAction SilentlyContinue
Remove-Item "$ws\vm" -Recurse -Force -ErrorAction SilentlyContinue

# --- build the unattend disk: a tiny FAT32 VHDX with autounattend.xml at its root ---
$uv = "$ws\unattend.vhdx"
New-VHD -Path $uv -SizeBytes 64MB -Dynamic | Out-Null
$disk = Mount-VHD -Path $uv -Passthru | Get-Disk
Initialize-Disk -Number $disk.Number -PartitionStyle MBR -PassThru | Out-Null
$part = New-Partition -DiskNumber $disk.Number -UseMaximumSize -AssignDriveLetter
Format-Volume -DriveLetter $part.DriveLetter -FileSystem FAT32 -NewFileSystemLabel UNATTEND -Confirm:$false | Out-Null
Copy-Item "$ws\autounattend.xml" ($part.DriveLetter + ":\autounattend.xml") -Force
Dismount-VHD -Path $uv
Log "unattend.vhdx built"

# --- create the VM ---
New-VM -Name $vm -Generation 2 -MemoryStartupBytes 8GB -Path "$ws\vm" `
  -NewVHDPath "$ws\vm\os.vhdx" -NewVHDSizeBytes 64GB | Out-Null
Set-VM $vm -AutomaticCheckpointsEnabled $false
Set-VMMemory $vm -DynamicMemoryEnabled $false
Set-VMProcessor $vm -Count 4
Add-VMDvdDrive $vm -Path "$ws\win11.iso"
Add-VMHardDiskDrive $vm -Path $uv   # auto-placed after OS(loc0)+DVD(loc1); OS remains DiskID 0
# vTPM (local key protector) + Secure Boot so Win11 installs without a hardware-check bypass
Set-VMKeyProtector $vm -NewLocalKeyProtector
Enable-VMTPM $vm
$dvd = Get-VMDvdDrive $vm
Set-VMFirmware $vm -FirstBootDevice $dvd -EnableSecureBoot On -SecureBootTemplate MicrosoftWindows
Log "VM created (Gen2, 8GB, 4 vCPU, 64GB OS, vTPM+SecureBoot, DVD first)"

Start-VM $vm
Log "VM started - unattended install running"
Log "STAGE2 DONE"
