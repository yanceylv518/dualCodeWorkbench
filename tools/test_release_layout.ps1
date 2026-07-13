[CmdletBinding()]
param(
    [switch]$RequireSidecar,
    [switch]$RequireDesktopArtifact,
    [switch]$RequireInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$TauriRoot = Join-Path $Root "apps\desktop\src-tauri"
$ConfigPath = Join-Path $TauriRoot "tauri.conf.json"
$CargoPath = Join-Path $TauriRoot "Cargo.toml"

function Assert-ReleaseCondition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Read-PackageVersion {
    param([string]$Path)
    return (Get-Content -Raw $Path | ConvertFrom-Json).version
}

Assert-ReleaseCondition (Test-Path $ConfigPath) "Missing Tauri configuration: $ConfigPath"
$Config = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$CargoText = Get-Content -Raw $CargoPath
$CargoVersionMatch = [regex]::Match($CargoText, '(?m)^version\s*=\s*"([^"]+)"')
Assert-ReleaseCondition $CargoVersionMatch.Success "Cargo package version is missing"
$BackendProjectText = Get-Content -Raw (Join-Path $Root "apps\backend\pyproject.toml")
$BackendProjectVersion = [regex]::Match($BackendProjectText, '(?m)^version\s*=\s*"([^"]+)"')
$BackendMainText = Get-Content -Raw (Join-Path $Root "apps\backend\dualcode\main.py")
$BackendApiVersion = [regex]::Match($BackendMainText, 'FastAPI\([^\r\n]*version="([^"]+)"')
Assert-ReleaseCondition $BackendProjectVersion.Success "Backend project version is missing"
Assert-ReleaseCondition $BackendApiVersion.Success "Backend API version is missing"

$Versions = @{
    root = Read-PackageVersion (Join-Path $Root "package.json")
    desktop = Read-PackageVersion (Join-Path $Root "apps\desktop\package.json")
    tauri = $Config.version
    cargo = $CargoVersionMatch.Groups[1].Value
    backend = $BackendProjectVersion.Groups[1].Value
    api = $BackendApiVersion.Groups[1].Value
}
$ExpectedVersion = $Versions.tauri
foreach ($Entry in $Versions.GetEnumerator()) {
    Assert-ReleaseCondition ($Entry.Value -eq $ExpectedVersion) "Version mismatch: $($Entry.Key)=$($Entry.Value), expected $ExpectedVersion"
}

Assert-ReleaseCondition ($Config.identifier -match '^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$') "Tauri bundle identifier is invalid"
Assert-ReleaseCondition ([bool]$Config.bundle.active) "Tauri bundling must be active"
Assert-ReleaseCondition ($Config.bundle.externalBin.Count -eq 1) "Exactly one external sidecar must be configured"
Assert-ReleaseCondition ($Config.bundle.externalBin[0] -eq 'binaries/dualcode-backend') "Unexpected externalBin mapping"
Assert-ReleaseCondition ($Config.bundle.resources.'binaries/dualcode-backend-runtime/' -eq 'dualcode-backend-runtime/') "Sidecar runtime resource mapping is missing"
Assert-ReleaseCondition ($Config.build.frontendDist -eq '../dist') "Unexpected frontendDist; release layout check must be updated"

$TargetTriple = "x86_64-pc-windows-msvc"
$SidecarExe = Join-Path $TauriRoot "binaries\dualcode-backend-$TargetTriple.exe"
$SidecarRuntime = Join-Path $TauriRoot "binaries\dualcode-backend-runtime"
if ($RequireSidecar) {
    Assert-ReleaseCondition (Test-Path $SidecarExe -PathType Leaf) "Missing sidecar executable: $SidecarExe"
    Assert-ReleaseCondition ((Get-Item $SidecarExe).Length -gt 0) "Sidecar executable is empty: $SidecarExe"
    Assert-ReleaseCondition (Test-Path $SidecarRuntime -PathType Container) "Missing sidecar runtime: $SidecarRuntime"
    Assert-ReleaseCondition ((Get-ChildItem $SidecarRuntime -Recurse -File).Count -gt 0) "Sidecar runtime is empty: $SidecarRuntime"
}

$ReleaseRoot = Join-Path $TauriRoot "target\release"
$DesktopExe = Join-Path $ReleaseRoot "dualcode-workbench.exe"
if ($RequireDesktopArtifact) {
    Assert-ReleaseCondition (Test-Path $DesktopExe -PathType Leaf) "Missing desktop release executable: $DesktopExe"
    Assert-ReleaseCondition ((Get-Item $DesktopExe).Length -gt 0) "Desktop release executable is empty: $DesktopExe"
}

$MsiInstallers = @(Get-ChildItem (Join-Path $ReleaseRoot 'bundle\msi\*.msi') -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "_$([regex]::Escape($ExpectedVersion))_x64_en-US\.msi$" })
$NsisInstallers = @(Get-ChildItem (Join-Path $ReleaseRoot 'bundle\nsis\*.exe') -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "_$([regex]::Escape($ExpectedVersion))_x64-setup\.exe$" })
$Installers = @($MsiInstallers) + @($NsisInstallers)
if ($RequireInstaller) {
    Assert-ReleaseCondition ($MsiInstallers.Count -eq 1) "Expected exactly one current-version MSI installer"
    Assert-ReleaseCondition ($NsisInstallers.Count -eq 1) "Expected exactly one current-version NSIS installer"
    foreach ($Installer in $Installers) {
        Assert-ReleaseCondition ($Installer.Length -gt 0) "Installer is empty: $($Installer.FullName)"
    }
}

Write-Host "[layout] version=$ExpectedVersion identifier=$($Config.identifier)" -ForegroundColor Green
if (Test-Path $SidecarExe) { Write-Host "[layout] sidecar=$SidecarExe" }
if (Test-Path $DesktopExe) { Write-Host "[layout] desktop=$DesktopExe" }
foreach ($Installer in $Installers) { Write-Host "[layout] installer=$($Installer.FullName)" }
