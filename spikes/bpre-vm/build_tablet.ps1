$img = Get-DiskImage -ImagePath 'D:\kookr-vm\ewdk.iso'; $drv = ($img | Get-Volume).DriveLetter
if(-not $drv){ $m=Mount-DiskImage -ImagePath 'D:\kookr-vm\ewdk.iso' -PassThru; Start-Sleep 2; $drv=($m|Get-Volume).DriveLetter }
$ep   = 'D:\kookr-vm\wds\audio\sysvad\EndpointsCommon\EndpointsCommon.vcxproj'
$proj = 'D:\kookr-vm\wds\audio\sysvad\TabletAudioSample\TabletAudioSample.vcxproj'
$flags = '/p:Configuration=Release /p:Platform=x64 /m /v:minimal /nologo'
$cmd = "call ${drv}:\BuildEnv\SetupBuildEnv.cmd && msbuild `"$ep`" $flags /t:Rebuild && msbuild `"$proj`" $flags /t:Rebuild"
cmd /c $cmd 2>&1 | Select-Object -Last 6
"=== MSBUILD EXIT: $LASTEXITCODE ==="
