[CmdletBinding()]
param(
    [switch]$BuildSidecar,
    [switch]$BuildDesktop,
    [switch]$BuildInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$BackendPython = Join-Path $Root "apps\backend\.venv\Scripts\python.exe"

function Invoke-Check {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "[verify] $Name" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $Action
    if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path $BackendPython)) {
    throw "Backend virtual environment is missing: $BackendPython"
}

Push-Location $Root
try {
    Invoke-Check "Release manifest and layout" {
        & "$PSScriptRoot\test_release_layout.ps1"
    }
    Invoke-Check "Backend tests" { & $BackendPython -m pytest apps\backend\tests -q -p no:cacheprovider }
    Invoke-Check "Desktop typecheck" { corepack pnpm --filter @dualcode/desktop typecheck }
    Invoke-Check "Desktop unit tests" { corepack pnpm --filter @dualcode/desktop test }
    Invoke-Check "Patch whitespace" { git diff --check }

    if ($BuildSidecar) {
        Invoke-Check "Backend sidecar build" {
            & "$PSScriptRoot\build_sidecar.ps1"
        }
        Invoke-Check "Sidecar release layout" {
            & "$PSScriptRoot\test_release_layout.ps1" -RequireSidecar
        }
    }

    if ($BuildDesktop -or $BuildInstaller) {
        $env:PATH = "$HOME\.cargo\bin;$env:PATH"
        $BuildArgs = @('pnpm', '--filter', '@dualcode/desktop', 'tauri', 'build')
        if (-not $BuildInstaller) { $BuildArgs += '--no-bundle' }
        Invoke-Check $(if ($BuildInstaller) { "Tauri installer build" } else { "Tauri release build" }) {
            & corepack @BuildArgs
        }
        Invoke-Check "Desktop release layout" {
            & "$PSScriptRoot\test_release_layout.ps1" -RequireSidecar -RequireDesktopArtifact -RequireInstaller:$BuildInstaller
        }
    }

    Write-Host "[verify] All requested release checks passed." -ForegroundColor Green
}
finally {
    Pop-Location
}
