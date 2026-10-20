# host_admin_sshd_revert.ps1 — tear down the elevated SSH admin channel created by
# host_admin_sshd_setup.ps1. Run from an ELEVATED PowerShell:
#   powershell -ExecutionPolicy Bypass -File "C:\Users\ibarz\BinauralHeadtracker\host_admin_sshd_revert.ps1"
#
# Removes Claude's authorized key, the firewall rule, and the token policy, and stops/disables
# sshd. Leaves the OpenSSH capability installed (harmless); uncomment the last block to remove it.

$ErrorActionPreference = 'Continue'
$log = 'C:\Users\ibarz\BinauralHeadtracker\revert_sshd.log'
Start-Transcript -Path $log -Force | Out-Null

Write-Output "stopping + disabling sshd"
Stop-Service sshd -ErrorAction SilentlyContinue
Set-Service sshd -StartupType Disabled -ErrorAction SilentlyContinue

Write-Output "removing authorized key file"
Remove-Item (Join-Path $env:ProgramData 'ssh\administrators_authorized_keys') -Force -ErrorAction SilentlyContinue

Write-Output "removing firewall rule"
Remove-NetFirewallRule -Name 'sshd-wsl' -ErrorAction SilentlyContinue

Write-Output "clearing LocalAccountTokenFilterPolicy"
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' `
  -Name LocalAccountTokenFilterPolicy -ErrorAction SilentlyContinue

# To fully remove OpenSSH Server too, uncomment:
# Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null

Write-Output "revert complete"
Stop-Transcript | Out-Null
