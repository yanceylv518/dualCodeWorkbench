$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "apps\backend\.venv\Scripts\python.exe"
$Output = Join-Path $Root "apps\desktop\src-tauri\binaries"
$Work = Join-Path $Root "apps\backend\build\pyinstaller"
$Spec = Join-Path $Root "apps\backend\build\spec"
if (-not (Test-Path $Python -PathType Leaf)) {
  throw "Backend virtual environment is missing: $Python"
}
New-Item -ItemType Directory -Force -Path $Output, $Work, $Spec | Out-Null
$Name = "dualcode-backend-x86_64-pc-windows-msvc"
$Built = Join-Path $Output $Name
$PublishedExe = Join-Path $Output "$Name.exe"
$Runtime = Join-Path $Output "dualcode-backend-runtime"
foreach ($StalePath in @($Built, $PublishedExe, $Runtime)) {
  if (Test-Path -LiteralPath $StalePath) { Remove-Item -Recurse -Force -LiteralPath $StalePath }
}
& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --windowed `
  --contents-directory "dualcode-backend-runtime" `
  --name $Name `
  --paths (Join-Path $Root "apps\backend") `
  --distpath $Output `
  --workpath $Work `
  --specpath $Spec `
  --collect-all uvicorn `
  --add-data "$(Join-Path $Root 'apps\backend\dualcode\alembic');dualcode/alembic" `
  --hidden-import aiosqlite `
  (Join-Path $Root "apps\backend\dualcode\sidecar.py")
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Copy-Item -Force (Join-Path $Built "$Name.exe") $PublishedExe
Move-Item (Join-Path $Built "dualcode-backend-runtime") $Runtime
Remove-Item -Recurse -Force $Built

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  (Join-Path $Root "tools\test_release_layout.ps1") -RequireSidecar
if ($LASTEXITCODE -ne 0) {
  throw "Sidecar release layout validation failed with exit code $LASTEXITCODE"
}
