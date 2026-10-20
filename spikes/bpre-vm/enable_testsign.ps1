$pw = ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('kookr', $pw)
Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock {
  $r = bcdedit /set testsigning on
  "bcdedit: $r"
  Start-Sleep 1
  Restart-Computer -Force
}
"testsigning enabled; guest rebooting"
