param([int]$Dev=21,[double]$Dur=20)
$pw=ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred=New-Object System.Management.Automation.PSCredential('kookr',$pw)
$s = New-PSSession -VMName kookr-bpre -Credential $cred
Copy-Item -ToSession $s -Path 'D:\kookr-vm\bpre_probe.py' -Destination 'C:\drv\bpre_probe.py' -Force
Invoke-Command -Session $s -ArgumentList $Dev,$Dur -ScriptBlock {
  param($d,$u)
  & 'C:\Program Files\Python312\python.exe' 'C:\drv\bpre_probe.py' $d $u 2>&1
}
Remove-PSSession $s
