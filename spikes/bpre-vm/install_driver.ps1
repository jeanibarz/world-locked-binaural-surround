$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
Invoke-Command -Session $s -ScriptBlock { New-Item -ItemType Directory -Force C:\drv | Out-Null }
Copy-Item -ToSession $s -Path 'D:\kookr-vm\pkg\*' -Destination 'C:\drv\' -Force
Copy-Item -ToSession $s -Path 'D:\kookr-vm\kookrtest.cer' -Destination 'C:\drv\kookrtest.cer' -Force
Copy-Item -ToSession $s -Path 'C:\Program Files (x86)\Windows Kits\10\Tools\10.0.26100.0\x64\devcon.exe' -Destination 'C:\drv\devcon.exe' -Force
Invoke-Command -Session $s -ScriptBlock {
  '--- trust cert (certutil) ---'
  certutil -addstore -f Root C:\drv\kookrtest.cer | Select-String -Pattern 'added|exist|command'
  certutil -addstore -f TrustedPublisher C:\drv\kookrtest.cer | Select-String -Pattern 'added|exist|command'
  '--- devcon install ---'
  & C:\drv\devcon.exe install C:\drv\ComponentizedAudioSample.inf 'Root\Sysvad_ComponentizedAudioSample' 2>&1
  Start-Sleep 5
  '--- SYSVAD device ---'
  Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'Virtual Audio|SYSVAD|Tablet' } | Select-Object Status,Class,FriendlyName | Format-Table -AutoSize | Out-String
  '--- audio render endpoints ---'
  Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue | Select-Object Status,FriendlyName | Format-Table -AutoSize | Out-String
}
Remove-PSSession $s
