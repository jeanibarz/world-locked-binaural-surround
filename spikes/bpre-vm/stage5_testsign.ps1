$ErrorActionPreference='Continue'
$log='D:\kookr-vm\build_stage5.log'
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8 }
$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
function WaitGuest($to=300){ $t=0; while($t -lt $to){ $r=Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock { $env:COMPUTERNAME } -ErrorAction SilentlyContinue; if($r){return $true}; Start-Sleep 10; $t+=10 }; return $false }
Log "STAGE5 START"

if(-not (WaitGuest)){ Log "guest not back after initial reboot - ABORT"; exit 1 }
Log "guest up"

$bl = Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock { (Get-BitLockerVolume -MountPoint 'C:' -ErrorAction SilentlyContinue).ProtectionStatus }
Log "BitLocker C: = $bl"
if("$bl" -eq 'On' -or "$bl" -eq '1'){ Log "BitLocker ON -> toggling Secure Boot would lock the VM; ABORT"; exit 2 }

Stop-VM kookr-bpre -Force
Set-VMFirmware kookr-bpre -EnableSecureBoot Off
Start-VM kookr-bpre
Log "Secure Boot disabled on VM; restarted"
if(-not (WaitGuest)){ Log "guest not back after SB-off - ABORT"; exit 3 }
Log "guest up (SB off)"

$r = Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock { bcdedit /set testsigning on }
Log "bcdedit testsigning: $r"
Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock { Restart-Computer -Force }
Start-Sleep 12
if(-not (WaitGuest)){ Log "guest not back after testsigning reboot - ABORT"; exit 4 }
$ts = Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock { if((bcdedit) -match 'testsigning\s+Yes'){'ON'}else{'OFF-or-unknown'} }
Log "testsigning verify: $ts"

Checkpoint-VM kookr-bpre -SnapshotName 'clean-testsigning'
Log "checkpoint 'clean-testsigning' taken"
Log "STAGE5 DONE"
