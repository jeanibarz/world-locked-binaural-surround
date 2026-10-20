# host_admin_sshd_setup.ps1 — one-time ELEVATED setup so the WSL dev session can SSH into this
# Windows host with a full admin token (for Track B: WDK install, Hyper-V VM, driver test-signing).
# Idempotent + fully logged. See AGENTS.md "Elevated host control — the SSH admin channel".
#
# Run from an ELEVATED PowerShell:
#   powershell -ExecutionPolicy Bypass -File "C:\Users\ibarz\BinauralHeadtracker\host_admin_sshd_setup.ps1"
#
# The authorized key below is the PUBLIC half of ~/.ssh/kookr_host_admin (private key stays in WSL,
# never leaves the machine). sshd ends up key-only + firewalled to the WSL subnet.

$ErrorActionPreference = 'Continue'
$log = 'C:\Users\ibarz\BinauralHeadtracker\setup_sshd.log'
Start-Transcript -Path $log -Force | Out-Null
function Step($m) { Write-Output "==== $m ====" }

Step "CONTEXT"
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = [Security.Principal.WindowsPrincipal]$id
Write-Output "User     : $($id.Name)"
Write-Output "Elevated : $($pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"

Step "OPENSSH SERVER"
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Set-Service sshd -StartupType Automatic
Start-Service sshd -ErrorAction SilentlyContinue
Write-Output "sshd : $((Get-Service sshd).Status)"

Step "AUTHORIZED KEY"
$akf = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
$key = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINclTn46uTKXUZI0OaXjPE7/i/+e2VqpIsuHy0qBDdPF kookr-claude-host-admin'
Set-Content -Path $akf -Value $key -Encoding ascii -NoNewline
Add-Content -Path $akf -Value "`n" -NoNewline
Write-Output "key lines: $((Get-Content $akf | Where-Object {$_ -ne ''}).Count) (expect 1)"

Step "ACL"   # SIDs, not names (host may be non-English: 'Administrators' won't resolve)
icacls $akf /setowner "*S-1-5-32-544" | Out-Null
icacls $akf /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F" | Out-Null
icacls $akf

Step "SSHD CONFIG + TOKEN POLICY"
$cfg = "$env:ProgramData\ssh\sshd_config"
(Get-Content $cfg) -replace '^#?PasswordAuthentication.*','PasswordAuthentication no' | Set-Content $cfg
Write-Output (Get-Content $cfg | Select-String 'PasswordAuthentication')
New-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' `
  -Name LocalAccountTokenFilterPolicy -Value 1 -PropertyType DWord -Force | Out-Null
Write-Output "LocalAccountTokenFilterPolicy = $((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System').LocalAccountTokenFilterPolicy)"

Step "FIREWALL"   # SSH only from the private WSL subnet
Remove-NetFirewallRule -Name 'sshd-wsl' -ErrorAction SilentlyContinue
New-NetFirewallRule -Name 'sshd-wsl' -DisplayName 'OpenSSH (WSL only)' -Enabled True `
  -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -RemoteAddress 172.16.0.0/12 | Out-Null
Write-Output "firewall rule 'sshd-wsl' -> 172.16.0.0/12"

Step "RESTART + LISTEN"
Restart-Service sshd
Start-Sleep -Seconds 1
Write-Output "sshd : $((Get-Service sshd).Status)"
Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 3 LocalAddress,LocalPort | Out-String | Write-Output

Step "DONE"
Stop-Transcript | Out-Null
Write-Output "Setup complete. Log: $log"
