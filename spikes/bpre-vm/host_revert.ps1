# host_revert.ps1  --  UNDO the host crash-safety attempt (run elevated on the HOST). Reboot after.
# Removes the device + driver package, turns test-signing off, and removes the test cert from the
# machine trust stores. Leaves the host exactly as it was before host_arm_testmode.ps1.
$ErrorActionPreference = 'Continue'
$devcon = 'C:\Program Files (x86)\Windows Kits\10\Tools\10.0.26100.0\x64\devcon.exe'
$hwid = 'Root\Sysvad_ComponentizedAudioSample'

Write-Output "--- remove device ---"
if(Test-Path $devcon){ & $devcon remove $hwid 2>&1 | Out-Null }
Start-Sleep 2
Write-Output "--- delete driver package from store ---"
$cur=$null
foreach($line in (pnputil /enum-drivers)){
  if($line -match 'Published Name\s*:\s*(oem\d+\.inf)'){ $cur=$matches[1] }
  if($line -match 'Original Name\s*:\s*componentizedaudiosample\.inf'){ Write-Output "deleting $cur"; pnputil /delete-driver $cur /uninstall /force 2>&1 | Out-Null }
}
Write-Output "--- test-signing OFF ---"
bcdedit /set testsigning off | Out-Null
bcdedit /enum '{current}' | Select-String 'testsigning'
Write-Output "--- remove test cert from machine trust stores ---"
$tp = '0BD7C22673BDAC05E61C6853E38730C746FDBC10'  # KookrTestDriver thumbprint (host CurrentUser\My)
foreach($store in 'Root','TrustedPublisher'){
  $p = "Cert:\LocalMachine\$store\$tp"
  if(Test-Path $p){ Remove-Item $p -Force; Write-Output "removed from $store" }
}
Write-Output ">>> Reverted. REBOOT to exit test mode. (The signing cert remains in CurrentUser\My for rebuilds.)"
