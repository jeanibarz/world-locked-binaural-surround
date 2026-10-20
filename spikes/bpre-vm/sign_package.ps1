$ErrorActionPreference='Stop'
$inf2cat  = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x86\Inf2Cat.exe'
$signtool = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe'
$rel = 'D:\kookr-vm\wds\audio\sysvad\TabletAudioSample\x64\Release'
$kwd = 'D:\kookr-vm\wds\audio\sysvad\KeywordDetectorAdapter\x64\Release\KeywordDetectorContosoAdapter.dll'
$pkg = 'D:\kookr-vm\pkg'
Remove-Item $pkg -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $pkg | Out-Null
Copy-Item "$rel\TabletAudioSample.sys" $pkg
Copy-Item "$rel\ComponentizedAudioSample.inf" $pkg
Copy-Item $kwd $pkg
# bump DriverVer version each build so Windows actually replaces the stored driver
$cf = 'D:\kookr-vm\drvver.txt'
$n = 1; if(Test-Path $cf){ $n = [int](Get-Content $cf) + 1 }
Set-Content $cf $n
$inf = "$pkg\ComponentizedAudioSample.inf"
(Get-Content $inf) -replace 'DriverVer\s*=.*',"DriverVer = 01/01/2024,10.0.26100.$n" | Set-Content $inf
"DriverVer version: 10.0.26100.$n"
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq 'CN=KookrTestDriver' } | Select-Object -First 1
$tp = $cert.Thumbprint
& $inf2cat /driver:$pkg /os:10_X64 2>&1 | Select-Object -Last 1
"inf2cat exit: $LASTEXITCODE"
$cat = Get-ChildItem $pkg -Filter *.cat -ErrorAction SilentlyContinue | Select-Object -First 1
if($cat){ foreach($f in @((Join-Path $pkg 'TabletAudioSample.sys'),(Join-Path $pkg 'KeywordDetectorContosoAdapter.dll'),$cat.FullName)){ & $signtool sign /fd sha256 /sha1 $tp $f 2>&1 | Out-Null }; "signed" } else { "NO CAT" }
