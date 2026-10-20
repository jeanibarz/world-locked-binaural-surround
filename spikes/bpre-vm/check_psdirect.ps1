$ErrorActionPreference = 'SilentlyContinue'
$pw = ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('kookr', $pw)
$r = Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock { $env:COMPUTERNAME } -ErrorAction SilentlyContinue
if ($r) { "READY $r" } else { "NOTREADY" }
