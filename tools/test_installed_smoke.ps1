[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [string]$InstallerPath,
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,
    [Parameter(Mandatory = $true)]
    [uri]$ApiBaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion,
    [switch]$AllowUnsignedDevelopmentInstaller,
    [int]$ApiTimeoutSeconds = 5
)

$ErrorActionPreference = "Stop"

function Assert-SmokeCondition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$ResolvedInstallRoot = (Resolve-Path -LiteralPath $InstallRoot -ErrorAction Stop).Path
$DesktopExe = Join-Path $ResolvedInstallRoot "dualcode-workbench.exe"
Assert-SmokeCondition (Test-Path -LiteralPath $DesktopExe -PathType Leaf) "Desktop executable is missing: $DesktopExe"
Assert-SmokeCondition ((Get-Item -LiteralPath $DesktopExe).Length -gt 0) "Desktop executable is empty: $DesktopExe"

if ($InstallerPath) {
    $ResolvedInstaller = (Resolve-Path -LiteralPath $InstallerPath -ErrorAction Stop).Path
    $Extension = [IO.Path]::GetExtension($ResolvedInstaller).ToLowerInvariant()
    Assert-SmokeCondition ($Extension -in @(".msi", ".exe")) "Installer must be an MSI or EXE: $ResolvedInstaller"
    Assert-SmokeCondition ((Get-Item -LiteralPath $ResolvedInstaller).Length -gt 0) "Installer is empty: $ResolvedInstaller"
    Assert-SmokeCondition ((Split-Path -Leaf $ResolvedInstaller) -match "_$([regex]::Escape($ExpectedVersion))_") `
        "Installer filename does not contain expected version $ExpectedVersion"
    $Signature = Get-AuthenticodeSignature -LiteralPath $ResolvedInstaller
    Assert-SmokeCondition ($Signature.Status -eq "Valid" -or $AllowUnsignedDevelopmentInstaller) `
        "Installer signature is $($Signature.Status); use -AllowUnsignedDevelopmentInstaller only for local development artifacts"
    Write-Host "[installed-smoke] installer=$ResolvedInstaller" -ForegroundColor Green
}

$Process = Get-Process -Id $ProcessId -ErrorAction Stop
$ActualPath = $Process.Path
Assert-SmokeCondition ([bool]$ActualPath) "Cannot determine executable path for process $ProcessId"
Assert-SmokeCondition ([IO.Path]::GetFullPath($ActualPath) -eq [IO.Path]::GetFullPath($DesktopExe)) `
    "Process $ProcessId is not running the expected installed executable: $ActualPath"
Assert-SmokeCondition (-not $Process.HasExited) "Installed desktop process has exited: $ProcessId"
Write-Host "[installed-smoke] process=$ProcessId" -ForegroundColor Green

Assert-SmokeCondition ($ApiBaseUrl.Scheme -eq "http") "Local sidecar smoke only accepts an http API URL"
Assert-SmokeCondition ($ApiBaseUrl.Host -in @("127.0.0.1", "localhost", "::1")) `
    "API URL must target loopback, not a remote service: $ApiBaseUrl"
$HealthUrl = [uri]::new($ApiBaseUrl, "/api/health")
$Health = Invoke-RestMethod -Method Get -Uri $HealthUrl -TimeoutSec $ApiTimeoutSeconds
Assert-SmokeCondition ($Health.status -eq "ok") "Sidecar health response was not ready: $($Health | ConvertTo-Json -Compress)"
$DiagnosticsUrl = [uri]::new($ApiBaseUrl, "/api/diagnostics")
$Diagnostics = Invoke-RestMethod -Method Get -Uri $DiagnosticsUrl -TimeoutSec $ApiTimeoutSeconds
Assert-SmokeCondition ($Diagnostics.version -eq $ExpectedVersion) "Sidecar version mismatch: $($Diagnostics.version)"
Assert-SmokeCondition ($Diagnostics.process.packaged -eq $true) "API is not served by a packaged sidecar instance"
Assert-SmokeCondition ([int]$Diagnostics.process.pid -gt 0) "Sidecar diagnostics did not provide a valid PID"
Write-Host "[installed-smoke] api=$HealthUrl status=ok sidecarPid=$($Diagnostics.process.pid)" -ForegroundColor Green

Write-Host "[installed-smoke] installRoot=$ResolvedInstallRoot" -ForegroundColor Green
Write-Host "[installed-smoke] PASS (no install, launch, or uninstall was performed)" -ForegroundColor Green
