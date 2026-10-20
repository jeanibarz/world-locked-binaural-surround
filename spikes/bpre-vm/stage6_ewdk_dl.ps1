[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$log='D:\kookr-vm\build_ewdk.log'
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8 }
Log "EWDK DL START"
try {
  Start-BitsTransfer -Source 'https://go.microsoft.com/fwlink/?linkid=2335681' -Destination 'D:\kookr-vm\ewdk.iso' -DisplayName kookrEWDK -ErrorAction Stop
  Log ("EWDK downloaded: " + [math]::Round((Get-Item 'D:\kookr-vm\ewdk.iso').Length/1GB,2) + " GB")
} catch { Log "EWDK DL error: $_" }
Log "EWDK DL DONE"
