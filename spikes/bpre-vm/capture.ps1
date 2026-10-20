param([int]$Dur=15)
$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
Copy-Item -ToSession $s -Path 'D:\kookr-vm\wav_analyze.py' -Destination 'C:\drv\wav_analyze.py' -Force
Copy-Item -ToSession $s -Path 'D:\kookr-vm\bpre_probe.py' -Destination 'C:\drv\bpre_probe.py' -Force
Invoke-Command -Session $s -ArgumentList $Dur -ScriptBlock {
  param($dur)
  $py='C:\Program Files\Python312\python.exe'
  $dir='C:\Windows\System32\drivers\DriverData\Audio_Samples\Sysvad'
  Remove-Item "$dir\*.wav" -Force -ErrorAction SilentlyContinue
  $dev = (& $py -c "import sounddevice as sd`nfor i,d in enumerate(sd.query_devices()):`n    if 'WASAPI' in sd.query_hostapis(d['hostapi'])['name'] and d['max_output_channels']>0 and 'Speakers' in d['name']: print(i); break") | Select-Object -First 1
  "Speakers dev=$dev; playing $dur s..."
  & $py 'C:\drv\bpre_probe.py' $dev $dur 2>&1 | Select-Object -Last 1
  Start-Sleep 2
  $w = Get-ChildItem "$dir\*.wav" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if($w){ "WAV: $($w.Name) ($([math]::Round($w.Length/1MB,2)) MB)"; & $py 'C:\drv\wav_analyze.py' $w.FullName } else { "NO WAV CREATED in $dir" }
}
Remove-PSSession $s
