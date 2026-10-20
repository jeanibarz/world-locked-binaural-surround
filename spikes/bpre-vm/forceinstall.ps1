$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
Copy-Item -ToSession $s -Path 'D:\kookr-vm\pkg\*' -Destination 'C:\drv\' -Force
Invoke-Command -Session $s -ScriptBlock {
  '--- remove device ---'
  & C:\drv\devcon.exe remove 'Root\Sysvad_ComponentizedAudioSample' 2>&1 | Out-Null
  Start-Sleep 3
  '--- delete old store packages ---'
  $cur=$null
  foreach($line in (pnputil /enum-drivers)){
    if($line -match 'Published Name\s*:\s*(oem\d+\.inf)'){ $cur=$matches[1] }
    if($line -match 'Original Name\s*:\s*componentizedaudiosample\.inf'){
      "deleting $cur"; pnputil /delete-driver $cur /uninstall /force 2>&1 | Select-String 'Deleting|deleted|success|fail|error'
    }
  }
  Start-Sleep 2
  '--- add new package to store ---'
  pnputil /add-driver C:\drv\ComponentizedAudioSample.inf /install 2>&1 | Select-String 'Adding|Published|success|fail|driver package'
  Start-Sleep 2
  '--- create device ---'
  & C:\drv\devcon.exe install C:\drv\ComponentizedAudioSample.inf 'Root\Sysvad_ComponentizedAudioSample' 2>&1 | Select-String 'successfully|failed'
  Start-Sleep 5
  '--- loaded hash ---'
  $store = Get-ChildItem 'C:\Windows\System32\DriverStore\FileRepository' -Recurse -Filter TabletAudioSample.sys -ErrorAction SilentlyContinue
  foreach($f in $store){ (Get-FileHash $f.FullName -Algorithm SHA256).Hash + "  " + $f.FullName }
}
Remove-PSSession $s
