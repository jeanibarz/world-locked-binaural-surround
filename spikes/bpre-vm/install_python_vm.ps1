$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
Invoke-Command -Session $s -ScriptBlock { New-Item -ItemType Directory -Force C:\py\wheels | Out-Null }
Copy-Item -ToSession $s -Path 'D:\kookr-vm\py.exe' -Destination 'C:\py\py.exe' -Force
Copy-Item -ToSession $s -Path 'D:\kookr-vm\wheels\*' -Destination 'C:\py\wheels\' -Force
Invoke-Command -Session $s -ScriptBlock {
  if(-not (Test-Path 'C:\Program Files\Python312\python.exe')){
    Start-Process C:\py\py.exe -ArgumentList '/quiet','InstallAllUsers=1','PrependPath=1','Include_pip=1' -Wait
  }
  $py='C:\Program Files\Python312\python.exe'
  'python installed: ' + (Test-Path $py)
  & $py -m pip install --no-index --find-links C:\py\wheels numpy sounddevice 2>&1 | Select-Object -Last 2
  & $py -c "import sounddevice as sd, numpy; print('sd', sd.__version__, '| WASAPI', any('WASAPI' in h['name'] for h in sd.query_hostapis()))"
}
Remove-PSSession $s
