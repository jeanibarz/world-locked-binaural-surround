param([int]$Dev=23)
$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
foreach($f in 'pseudo_k11.py','bpre_probe.py'){ Copy-Item -ToSession $s -Path "D:\kookr-vm\$f" -Destination "C:\drv\$f" -Force }
$r = Invoke-Command -Session $s -ArgumentList $Dev -ScriptBlock {
  param($dev)
  $py='C:\Program Files\Python312\python.exe'
  # 15 rapid start/stop cycles with the ticker (exercises thread+event create/destroy)
  for($i=1;$i -le 15;$i++){
    $p = Start-Process -FilePath $py -ArgumentList 'C:\drv\pseudo_k11.py','3','10' -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 400
    & $py 'C:\drv\bpre_probe.py' $dev 1.5 2>&1 | Out-Null
    Start-Sleep -Milliseconds 200
  }
  'cycles done'
  # watchdog test: start a play, kill the ticker mid-stream -> free-run must not crash
  $p = Start-Process -FilePath $py -ArgumentList 'C:\drv\pseudo_k11.py','6','10' -WindowStyle Hidden -PassThru
  Start-Sleep -Milliseconds 500
  Start-Job { param($py,$pid2) Start-Sleep 2; Stop-Process -Id $pid2 -Force -EA SilentlyContinue } -ArgumentList $py,$p.Id | Out-Null
  & $py 'C:\drv\bpre_probe.py' $dev 4 2>&1 | Select-Object -Last 1
  'watchdog test done'
}
$r
Remove-PSSession $s
