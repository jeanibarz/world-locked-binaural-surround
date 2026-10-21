param([int]$Dev=23)
$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
foreach($f in 'consumer_dryrun.py','bpre_probe.py','wav_analyze.py'){ Copy-Item -ToSession $s -Path "D:\kookr-vm\$f" -Destination "C:\drv\$f" -Force }
Invoke-Command -Session $s -ArgumentList $Dev -ScriptBlock {
  param($dev)
  $py='C:\Program Files\Python312\python.exe'
  $dir='C:\Windows\System32\drivers\DriverData\Audio_Samples\Sysvad'
  Remove-Item "$dir\*.wav" -Force -EA SilentlyContinue
  # consumer dry-run ticks KookrPullTick from a real render callback (10ms blocks)
  Start-Process -FilePath $py -ArgumentList 'C:\drv\consumer_dryrun.py','16','480' -WindowStyle Hidden -RedirectStandardOutput 'C:\drv\consumer.log'
  Start-Sleep 2
  'playing tone into the pull-driven device (ticked by the render callback)...'
  & $py 'C:\drv\bpre_probe.py' $dev 12 2>&1 | Select-Object -Last 1
  Start-Sleep 3
  '--- consumer dry-run log ---'; Get-Content 'C:\drv\consumer.log' -EA SilentlyContinue
  $w = Get-ChildItem "$dir\*.wav" -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if($w){ "WAV: $($w.Name) ($([math]::Round($w.Length/1MB,2)) MB)"; & $py 'C:\drv\wav_analyze.py' $w.FullName } else { "NO WAV" }
}
Remove-PSSession $s
