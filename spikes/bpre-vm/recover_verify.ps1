param([int]$Dev=23)
$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
foreach($f in 'pseudo_k11.py','bpre_probe.py','wav_analyze.py'){ Copy-Item -ToSession $s -Path "D:\kookr-vm\$f" -Destination "C:\drv\$f" -Force }
Invoke-Command -Session $s -ArgumentList $Dev -ScriptBlock {
  param($dev)
  $py='C:\Program Files\Python312\python.exe'
  "=== BUGCHECK / UNEXPECTED-SHUTDOWN EVENT LOG (since last boot) ==="
  $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
  "last boot: $boot"
  $ev = Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,1001,6008} -EA SilentlyContinue |
        Where-Object { $_.TimeCreated -ge $boot.AddHours(-2) }
  if($ev){ $ev | Select-Object TimeCreated,Id,ProviderName | Format-Table -AutoSize | Out-String }
  else { "NO bugcheck (1001) / kernel-power-unexpected (41) / dirty-shutdown (6008) events -> no BSOD" }
  "=== RECOVERY CLEAN PLAY (ticker running, after the consumer-drop test) ==="
  $before = (Get-ChildItem 'C:\drv\SaveData*.wav' -EA SilentlyContinue | Measure-Object).Count
  Start-Process -FilePath $py -ArgumentList 'C:\drv\pseudo_k11.py','8','10' -WindowStyle Hidden | Out-Null
  Start-Sleep -Milliseconds 800
  & $py 'C:\drv\bpre_probe.py' $dev 5 2>&1 | Select-Object -Last 2
  Start-Sleep 1
  $w = Get-ChildItem 'C:\drv\SaveData*.wav' | Sort-Object LastWriteTime | Select-Object -Last 1
  "newest WAV: $($w.Name) ($([math]::Round($w.Length/1KB))KB)"
  & $py 'C:\drv\wav_analyze.py' $w.FullName 2>&1
}
Remove-PSSession $s
