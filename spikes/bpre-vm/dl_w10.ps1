$ErrorActionPreference='Continue'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$ws='D:\kookr-vm'; $log="$ws\w10_dl.log"
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File $log -Append -Encoding utf8 }
"=== dl_w10 start $(Get-Date -Format s) ===" | Set-Content $log
if((Test-Path "$ws\win10.iso") -and ((Get-Item "$ws\win10.iso").Length -gt 4GB)){ Log "win10.iso already present"; return }
$fido="$ws\Fido.ps1"
Log "resolving fresh Win10 22H2 URL via Fido..."
$out = & $fido -Win 10 -Rel 22H2 -Ed Pro -Lang English -Arch x64 -GetUrl 2>&1
$url = ($out | Where-Object { $_ -match '^https?://' } | Select-Object -Last 1)
if(-not $url){ Log ("Fido gave no URL: " + ($out -join ' | ')); return }
Log "URL ok; downloading with curl.exe ..."
& curl.exe -L --retry 3 --retry-delay 5 -o "$ws\win10.iso" $url 2>&1 | Out-Null
if((Test-Path "$ws\win10.iso")){ Log ("win10.iso size GB: " + [math]::Round((Get-Item "$ws\win10.iso").Length/1GB,2)) } else { Log "win10.iso MISSING after curl" }
Log "dl_w10 done"
