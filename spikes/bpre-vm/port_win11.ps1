# port_win11.ps1 -- re-catalog + re-sign D:\kookr-vm\pkg (the 22621 INF, covers Win11 26xxx) after
# staging a freshly-built TabletAudioSample.sys into it. Bumps DriverVer so the store replaces the
# prior package. Non-elevated; signing only. The install (forceinstall.ps1) is the next step.
$ErrorActionPreference = 'Stop'
$inf2cat  = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x86\Inf2Cat.exe'
$signtool = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe'
$pkg = 'D:\kookr-vm\pkg'
$inf = "$pkg\ComponentizedAudioSample.inf"

Write-Output "=== re-sign Win11 pkg (22621 INF) ==="
$txt = Get-Content $inf -Raw
$cf = 'D:\kookr-vm\drvver11.txt'
$n = 200; if (Test-Path $cf) { $n = [int](Get-Content $cf) + 1 }
Set-Content $cf $n
$txt = $txt -replace 'DriverVer\s*=.*', "DriverVer = 01/01/2024,10.0.22621.$n"
Set-Content $inf $txt -NoNewline
Write-Output "DriverVer = 10.0.22621.$n"
Select-String -Path $inf -Pattern 'NTamd64\.10\.0|DriverVer' | ForEach-Object { '  ' + $_.Line.Trim() }

& $inf2cat /driver:$pkg /os:10_X64 2>&1 | Select-Object -Last 3
Write-Output "inf2cat exit: $LASTEXITCODE"
if ($LASTEXITCODE -ne 0) { throw "inf2cat failed" }

$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq 'CN=KookrTestDriver' } | Select-Object -First 1
if (-not $cert) { throw "KookrTestDriver cert not found" }
$tp = $cert.Thumbprint
$cat = Get-ChildItem $pkg -Filter *.cat | Select-Object -First 1
foreach ($f in @("$pkg\TabletAudioSample.sys", "$pkg\KeywordDetectorContosoAdapter.dll", $cat.FullName)) {
    & $signtool sign /fd sha256 /sha1 $tp $f 2>&1 | Out-Null
}
Write-Output "signed (cert $tp); catalog = $($cat.Name)"
Write-Output "sys sha256 = $((Get-FileHash "$pkg\TabletAudioSample.sys" -Algorithm SHA256).Hash)"
Write-Output ">>> pkg ready for Win11 install (forceinstall.ps1 -> kookr-bpre)"
