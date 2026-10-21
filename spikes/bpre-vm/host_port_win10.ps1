# host_port_win10.ps1  --  port the SYSVAD package to Windows 10 and re-sign (run on the HOST).
#
# The validated package (D:\kookr-vm\pkg) gates its models section on NTamd64.10.0...22621 (build >=
# 22621 = Win11 22H2+), so it will not bind on this Win10 19045 host (journal 65). This builds a
# Win10 variant in D:\kookr-vm\pkg10: same byte-identical TabletAudioSample.sys, but the INF target
# lowered to build 19041 (covers 19045 AND Win11), DriverVer bumped, catalog regenerated for Win10
# x64, everything re-signed with the KookrTestDriver test cert.
#
# Non-elevated; signing only. The install (devcon) is a separate elevated step.
$ErrorActionPreference = 'Stop'
$inf2cat  = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x86\Inf2Cat.exe'
$signtool = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe'
$src = 'D:\kookr-vm\pkg'
$pkg = 'D:\kookr-vm\pkg10'

Write-Output "=== port SYSVAD package to Win10 (build >= 19041) ==="
Remove-Item $pkg -Recurse -Force -EA SilentlyContinue
New-Item -ItemType Directory $pkg | Out-Null
Copy-Item "$src\TabletAudioSample.sys" $pkg
Copy-Item "$src\KeywordDetectorContosoAdapter.dll" $pkg
Copy-Item "$src\ComponentizedAudioSample.inf" $pkg
$inf = "$pkg\ComponentizedAudioSample.inf"

# 1. lower the OS gate 22621 -> 19041 (both the [Manufacturer] line and the model section header)
$txt = Get-Content $inf -Raw
$before = ([regex]::Matches($txt, '10\.0\.\.\.22621')).Count
$txt = $txt -replace '10\.0\.\.\.22621', '10.0...19041'
# 2. bump DriverVer so the driver store actually replaces any prior package
$cf = 'D:\kookr-vm\drvver10.txt'
$n = 100; if (Test-Path $cf) { $n = [int](Get-Content $cf) + 1 }
Set-Content $cf $n
$txt = $txt -replace 'DriverVer\s*=.*', "DriverVer = 01/01/2024,10.0.19041.$n"
Set-Content $inf $txt -NoNewline
Write-Output "gate 22621->19041 replacements: $before ; DriverVer = 10.0.19041.$n"
Write-Output "--- INF Manufacturer/model now ---"
Select-String -Path $inf -Pattern 'NTamd64\.10\.0|DriverVer' | ForEach-Object { '  ' + $_.Line.Trim() }

# 3. regenerate the catalog for Win10 x64
& $inf2cat /driver:$pkg /os:10_X64 2>&1 | Select-Object -Last 3
Write-Output "inf2cat exit: $LASTEXITCODE"
if ($LASTEXITCODE -ne 0) { throw "inf2cat failed" }

# 4. re-sign .sys/.dll/.cat with the test cert
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq 'CN=KookrTestDriver' } | Select-Object -First 1
if (-not $cert) { throw "KookrTestDriver cert not found in CurrentUser\My" }
$tp = $cert.Thumbprint
$cat = Get-ChildItem $pkg -Filter *.cat | Select-Object -First 1
foreach ($f in @("$pkg\TabletAudioSample.sys", "$pkg\KeywordDetectorContosoAdapter.dll", $cat.FullName)) {
    & $signtool sign /fd sha256 /sha1 $tp $f 2>&1 | Out-Null
}
Write-Output "signed (cert $tp); catalog = $($cat.Name)"
Write-Output "sys sha256 = $((Get-FileHash "$pkg\TabletAudioSample.sys" -Algorithm SHA256).Hash)"
Write-Output ">>> pkg10 ready. Next (elevated): devcon install D:\kookr-vm\pkg10\ComponentizedAudioSample.inf Root\Sysvad_ComponentizedAudioSample"
