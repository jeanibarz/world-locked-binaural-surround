$vswhere="${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$msb = & $vswhere -latest -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe | Select-Object -First 1
if(-not $msb){ "MSBUILD NOT FOUND"; exit 1 }
"msbuild: $msb"
& $msb "D:\kookr-vm\wds\audio\sysvad\sysvad.sln" /p:Configuration=Release /p:Platform=x64 /t:Rebuild /m /v:minimal /nologo 2>&1
"=== MSBUILD EXIT: $LASTEXITCODE ==="
