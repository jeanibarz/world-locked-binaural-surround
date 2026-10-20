$ErrorActionPreference='SilentlyContinue'
$pw = ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('kookr', $pw)
Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock {
  $os = Get-CimInstance Win32_OperatingSystem
  "computer=$env:COMPUTERNAME"
  "os=$($os.Caption) build $($os.BuildNumber)"
  "user=$(whoami)"
  "admin=$([bool](([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')))"
}
