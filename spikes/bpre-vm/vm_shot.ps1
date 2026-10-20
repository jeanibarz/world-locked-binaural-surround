param([string]$Vm='kookr-bpre',[string]$Out='D:\kookr-vm\vm_shot.png')
Add-Type -AssemblyName System.Drawing
$ns='root\virtualization\v2'
$vmms=Get-CimInstance -Namespace $ns -ClassName Msvm_VirtualSystemManagementService
$cs=Get-CimInstance -Namespace $ns -ClassName Msvm_ComputerSystem -Filter "ElementName='$Vm'"
$vh=Get-CimInstance -Namespace $ns -ClassName Msvm_VideoHead | Where-Object { $_.SystemName -eq $cs.Name } | Select-Object -First 1
$W=[int]$vh.CurrentHorizontalResolution; $H=[int]$vh.CurrentVerticalResolution
if(-not $W -or -not $H){ $W=1024; $H=768 }
"resolution: $W x $H"
$vssd=Get-CimAssociatedInstance -InputObject $cs -ResultClassName Msvm_VirtualSystemSettingData |
  Where-Object { $_.VirtualSystemType -eq 'Microsoft:Hyper-V:System:Realized' } | Select-Object -First 1
$r=Invoke-CimMethod -InputObject $vmms -MethodName GetVirtualSystemThumbnailImage -Arguments @{
  TargetSystem=$vssd; WidthPixels=[uint16]$W; HeightPixels=[uint16]$H }
if(-not $r.ImageData){ "NO_IMAGE rc=$($r.ReturnValue)"; exit 1 }
$bytes=[byte[]]$r.ImageData
"imagedata bytes: $($bytes.Length) expected: $($W*$H*2)"
$bmp=New-Object System.Drawing.Bitmap($W,$H,[System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
$rect=New-Object System.Drawing.Rectangle(0,0,$W,$H)
$bd=$bmp.LockBits($rect,[System.Drawing.Imaging.ImageLockMode]::WriteOnly,[System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
$n=[Math]::Min($bytes.Length, $bd.Stride*$H)
[System.Runtime.InteropServices.Marshal]::Copy($bytes,0,$bd.Scan0,$n)
$bmp.UnlockBits($bd)
$bmp.Save($Out,[System.Drawing.Imaging.ImageFormat]::Png)
"SAVED $Out"
