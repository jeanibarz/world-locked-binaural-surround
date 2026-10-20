# stage4_vm.ps1 — recreate the VM from the REMASTERED ISO (autounattend at root + no-prompt boot).
# Fully unattended: no secondary unattend disk, no keystrokes. Logs to build_stage4.log.
$ErrorActionPreference = 'Stop'
$ws = 'D:\kookr-vm'; $vm = 'kookr-bpre'
$log = "$ws\build_stage4.log"
function Log($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8; Write-Output $m }
Log "STAGE4 START"

if (Get-VM -Name $vm -ErrorAction SilentlyContinue) {
  Stop-VM $vm -TurnOff -Force -ErrorAction SilentlyContinue
  Remove-VM $vm -Force
  Log "removed existing VM"
}
Remove-Item "$ws\vm" -Recurse -Force -ErrorAction SilentlyContinue

New-VM -Name $vm -Generation 2 -MemoryStartupBytes 8GB -Path "$ws\vm" -NewVHDPath "$ws\vm\os.vhdx" -NewVHDSizeBytes 64GB | Out-Null
Set-VM $vm -AutomaticCheckpointsEnabled $false
Set-VMMemory $vm -DynamicMemoryEnabled $false
Set-VMProcessor $vm -Count 4
Add-VMDvdDrive $vm -Path "$ws\win11_auto.iso"
Set-VMKeyProtector $vm -NewLocalKeyProtector
Enable-VMTPM $vm
$dvd = Get-VMDvdDrive $vm
Set-VMFirmware $vm -FirstBootDevice $dvd -EnableSecureBoot On -SecureBootTemplate MicrosoftWindows
Log "VM recreated from win11_auto.iso (Gen2, vTPM+SecureBoot)"
Start-VM $vm
Log "VM started - unattended install from remastered ISO"
Log "STAGE4 DONE"
