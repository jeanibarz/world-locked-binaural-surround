param([int]$Dev=22)
$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
foreach($f in 'pseudo_k11.py','bpre_probe.py','wav_analyze.py'){ Copy-Item -ToSession $s -Path "D:\kookr-vm\$f" -Destination "C:\drv\$f" -Force }
Invoke-Command -Session $s -ArgumentList $Dev -ScriptBlock {
  param($dev)
  $py='C:\Program Files\Python312\python.exe'
  $dir='C:\Windows\System32\drivers\DriverData\Audio_Samples\Sysvad'
  Remove-Item "$dir\*.wav" -Force -ErrorAction SilentlyContinue
  # start the pseudo-K11 ticker in the background (it waits for the stream's event)
  Start-Process -FilePath $py -ArgumentList 'C:\drv\pseudo_k11.py','20','10' -WindowStyle Hidden -RedirectStandardOutput 'C:\drv\k11.log'
  Start-Sleep 2
  'playing tone (device pull-driven by pseudo-K11)...'
  & $py 'C:\drv\bpre_probe.py' $dev 14 2>&1 | Select-Object -Last 1
  Start-Sleep 3
  '--- pseudo-K11 log ---'; Get-Content 'C:\drv\k11.log' -ErrorAction SilentlyContinue
  $w = Get-ChildItem "$dir\*.wav" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if($w){ "WAV: $($w.Name) ($([math]::Round($w.Length/1MB,2)) MB)"; & $py 'C:\drv\wav_analyze.py' $w.FullName } else { "NO WAV" }
}
Remove-PSSession $s
