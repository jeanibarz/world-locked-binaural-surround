# host_arm_testmode.ps1  --  STEP 1 of the host crash-safety attempt (run elevated on the HOST).
#
# Arms the host to load the test-signed Kookr/SYSVAD driver. Does NOT reboot (the reboot is the
# operator's deliberate choice: it also restarts WSL and any Claude Code session running on this host).
# Fully reversible with host_revert.ps1.
#
# Safe on THIS host because: legacy BIOS (no Secure Boot) + BitLocker OFF -> enabling test-signing
# triggers no recovery-key prompt and cannot lock the machine. Verified in journal 61.
$ErrorActionPreference = 'Stop'
Write-Output "=== Kookr host arm: trust the test cert + enable test-signing ==="

# 1. Import the KookrTestDriver PUBLIC cert into the machine trust stores so the test-signed driver loads.
$c = Get-ChildItem Cert:\CurrentUser\My | Where-Object Subject -match 'KookrTestDriver' | Select-Object -First 1
if (-not $c) { throw "KookrTestDriver signing cert not found in CurrentUser\My" }
$tmp = "$env:TEMP\kookrtest.cer"
Export-Certificate -Cert $c -FilePath $tmp -Force | Out-Null
foreach ($store in 'Root','TrustedPublisher') {
    Import-Certificate -FilePath $tmp -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null
    Write-Output "cert -> LocalMachine\$store (thumbprint $($c.Thumbprint))"
}
Remove-Item $tmp -Force -EA SilentlyContinue

# 2. Enable test-signing (effective after the next reboot). No nointegritychecks (ignored on many systems).
bcdedit /set testsigning on | Out-Null
Write-Output "bcdedit testsigning -> ON (effective after reboot)"
bcdedit /enum '{current}' | Select-String 'testsigning'

# 3. Drop an armed-at marker so host_install_and_test.ps1 can prove the kernel actually rebooted
#    AFTER this (testsigning=Yes alone is only the configured value; the running kernel enforces
#    signatures until the next boot — journal 66).
$armFile = 'D:\kookr-vm\testmode_armed_at.txt'
(Get-Date).ToString('o') | Set-Content $armFile
Write-Output "armed-at marker -> $armFile ($(Get-Date -Format s))"

Write-Output ""
Write-Output ">>> ARMED. Next: do a full Windows RESTART of the host (not shutdown+power-on; that can skip"
Write-Output ">>> the config via Fast Startup). This also restarts WSL + any Claude session on it."
Write-Output ">>> After reboot, run:  powershell -ExecutionPolicy Bypass -File D:\kookr-vm\host_install_and_test.ps1"
Write-Output ">>> To undo everything:  powershell -ExecutionPolicy Bypass -File D:\kookr-vm\host_revert.ps1  (then reboot)"
