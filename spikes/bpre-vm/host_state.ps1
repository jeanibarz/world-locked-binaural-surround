Write-Output "=== HOST Secure Boot / BitLocker / test-signing state ==="
try { $sb = Confirm-SecureBootUEFI; Write-Output "SecureBoot enabled: $sb" } catch { Write-Output "SecureBoot query: $($_.Exception.Message)" }
Write-Output "--- BitLocker (system drive) ---"
try { Get-BitLockerVolume -MountPoint C: | Select-Object MountPoint,VolumeStatus,ProtectionStatus,EncryptionPercentage | Format-List } catch { Write-Output "BitLocker query: $($_.Exception.Message)" }
Write-Output "--- bcdedit (testsigning / integrity) ---"
$cur = bcdedit /enum '{current}'
$cur | Select-String 'testsigning|nointegritychecks|flightsigning'
if(-not ($cur | Select-String 'testsigning')){ Write-Output "testsigning: (not set -> OFF)" }
Write-Output "--- any Kookr/Sysvad driver already on host? ---"
$d = pnputil /enum-drivers
$hit = $d | Select-String 'ComponentizedAudioSample|Sysvad|Kookr'
if($hit){ $hit } else { Write-Output "none installed" }
