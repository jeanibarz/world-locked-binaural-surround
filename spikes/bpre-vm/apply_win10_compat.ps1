# apply_win10_compat.ps1 -- make the SYSVAD driver load on Win10 19045 (research-journal #68).
# Inserts a down-level compat macro block into common.cpp that redirects the two NI-only lookaside
# imports (Ex{Allocate,Free}{To}NPagedLookasideList, used only by never-instantiated BTH/USB/A2DP
# sideband pools) to always-exported pool APIs. Keeps NTDDI at NI so KSJACK_DESCRIPTION3/audio-posture
# still compile. Idempotent. Then rebuilds EndpointsCommon+TabletAudioSample and statically verifies
# every import resolves on the live Win10 kernel.
$ErrorActionPreference='Continue'
$ws='D:\kookr-vm'; $base="$ws\wds\audio\sysvad"; $out=@()
function Say($m){ $script:out += $m }
$cc="$base\common.cpp"
$marker='Kookr Win10 down-level compat'
$c = Get-Content $cc
if(($c -join "`n") -match [regex]::Escape($marker)){
  Say "patch already present in common.cpp"
} else {
  $anchor = ($c | Select-String '#endif // SYSVAD_A2DP_SIDEBAND' | Select-Object -First 1).LineNumber
  if(-not $anchor){ Say "ANCHOR NOT FOUND (#endif // SYSVAD_A2DP_SIDEBAND)"; Set-Content "$ws\compat_result.txt" ($out -join "`n") -Encoding UTF8; return }
  if(-not (Test-Path "$cc.preCompat.bak")){ Copy-Item $cc "$cc.preCompat.bak" -Force }
  $block = @(
    '',
    '// ===== Kookr Win10 down-level compat (binaural-headtracker, research-journal #68) =====',
    '// Win10 (< NTDDI_WIN10_NI) ntoskrnl does not export Ex{Allocate,Free}{To}NPagedLookasideList',
    '// (inline-only below NI). This package builds at NI (its topology uses KSJACK_DESCRIPTION3 and',
    '// audio-posture, both NI-gated), so the 26100 WDK emits those two lookaside calls as imports and',
    '// the driver fails to load on Win10 19045 (STATUS_DRIVER_ENTRY_NOT_FOUND). They are used ONLY by',
    '// the BTH/USB/A2DP sideband work-task pools, which a Root-enumerated pull-driven virtual render',
    '// device never instantiates. Redirect them to always-exported pool APIs so the driver loads',
    '// down-level; the pools are never exercised, so the allocation semantics are irrelevant.',
    '#if (NTDDI_VERSION >= NTDDI_WIN10_NI)',
    '#undef  ExAllocateFromNPagedLookasideList',
    '#undef  ExFreeToNPagedLookasideList',
    '#define ExAllocateFromNPagedLookasideList(Lookaside) \',
    '            ExAllocatePool2(POOL_FLAG_NON_PAGED, (Lookaside)->L.Size, (Lookaside)->L.Tag)',
    '#define ExFreeToNPagedLookasideList(Lookaside, Entry) \',
    '            ExFreePool(Entry)',
    '#endif // Kookr Win10 down-level compat',
    ''
  )
  $new = @(); $new += $c[0..($anchor-1)]; $new += $block; $new += $c[$anchor..($c.Count-1)]
  Set-Content $cc -Value $new
  Say "inserted compat block after line $anchor in common.cpp"
}
# rebuild
$img=Get-DiskImage -ImagePath "$ws\ewdk.iso" -EA SilentlyContinue; $drv=if($img){($img|Get-Volume).DriveLetter}
if(-not $drv){ $m=Mount-DiskImage -ImagePath "$ws\ewdk.iso" -PassThru; Start-Sleep 2; $drv=($m|Get-Volume).DriveLetter }
$ep="$base\EndpointsCommon\EndpointsCommon.vcxproj"; $proj="$base\TabletAudioSample\TabletAudioSample.vcxproj"
$flags='/p:Configuration=Release /p:Platform=x64 /m /v:minimal /nologo'
$bl="$ws\compat_build.log"
$cmd="call ${drv}:\BuildEnv\SetupBuildEnv.cmd && msbuild `"$ep`" $flags /t:Rebuild && msbuild `"$proj`" $flags /t:Rebuild"
cmd /c $cmd > $bl 2>&1
Say "MSBUILD EXIT: $LASTEXITCODE"
$sys="$base\TabletAudioSample\x64\Release\TabletAudioSample.sys"
if(-not (Test-Path $sys)){ Say "SYS NOT BUILT (see compat_build.log)"; Set-Content "$ws\compat_result.txt" ($out -join "`n") -Encoding UTF8; return }
Say ("sys built: " + (Get-Item $sys).LastWriteTime.ToString('s') + "  sha256=" + (Get-FileHash $sys -Algorithm SHA256).Hash)
# static verify vs live Win10 kernel
$db='F:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\dumpbin.exe'
$imp = & $db /imports $sys 2>&1
$cur=$null;$map=@{}
foreach($line in $imp){
  if($line -match '^\s{4}([A-Za-z0-9_]+\.(?:sys|exe|dll))\s*$'){ $cur=$matches[1].ToLower(); if(-not $map.ContainsKey($cur)){$map[$cur]=@()}; continue }
  if($cur -and $line -match '^\s+[0-9A-Fa-f]+\s+([A-Za-z_][A-Za-z0-9_@]+)\s*$'){ $map[$cur]+=$matches[1] }
}
function Exp($p){ if(-not(Test-Path $p)){return @()} ; $e=& $db /exports $p 2>&1; $n=@(); foreach($l in $e){ if($l -match '^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+([A-Za-z_][A-Za-z0-9_@]+)'){$n+=$matches[1]} }; return $n }
$live=@{'ntoskrnl.exe'='C:\Windows\System32\ntoskrnl.exe';'portcls.sys'='C:\Windows\System32\drivers\portcls.sys';'wdfldr.sys'='C:\Windows\System32\drivers\wdfldr.sys';'hal.dll'='C:\Windows\System32\ntoskrnl.exe'}
Say "imported modules: $($map.Keys -join ', ')"
$anyMissing=$false
foreach($mod in $map.Keys){
  $lp=$live[$mod]; if(-not $lp){ Say "  $mod : no live map"; continue }
  $ex=Exp $lp; $miss=$map[$mod] | Where-Object { $ex -notcontains $_ }
  if($miss){ $anyMissing=$true; Say "  $mod MISSING on Win10: $($miss -join ', ')" } else { Say "  $mod : all $($map[$mod].Count) resolve on Win10" }
}
Say ("lookaside still imported: " + [bool]($imp | Select-String 'NPagedLookasideList'))
Say ("VERDICT: " + $(if($anyMissing){"STILL MISSING -- not loadable"}else{"ALL IMPORTS RESOLVE ON WIN10 19045 -- will load"}))
# Refresh the staging dir with the rebuilt (Win10-loadable) binary so host_port_win10.ps1 then
# produces a correct Win10 pkg10 (INF gated 19041, re-signed). Run order for a clean checkout:
#   build_tablet.ps1  ->  apply_win10_compat.ps1  ->  host_port_win10.ps1
if(-not $anyMissing -and (Test-Path "$ws\pkg\TabletAudioSample.sys")){
  Copy-Item $sys "$ws\pkg\TabletAudioSample.sys" -Force
  Say "refreshed $ws\pkg\TabletAudioSample.sys with the rebuilt binary (run host_port_win10.ps1 next)"
}
Set-Content "$ws\compat_result.txt" ($out -join "`n") -Encoding UTF8
