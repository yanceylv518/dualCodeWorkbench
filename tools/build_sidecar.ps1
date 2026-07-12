$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "apps\backend\.venv\Scripts\python.exe"
$Output = Join-Path $Root "apps\desktop\src-tauri\binaries"
$Work = Join-Path $Root "apps\backend\build\pyinstaller"
$Spec = Join-Path $Root "apps\backend\build\spec"
New-Item -ItemType Directory -Force -Path $Output, $Work, $Spec | Out-Null
$Name = "dualcode-backend-x86_64-pc-windows-msvc"
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
  --hidden-import aiosqlite `
  (Join-Path $Root "apps\backend\dualcode\sidecar.py")

$Built = Join-Path $Output $Name
Copy-Item -Force (Join-Path $Built "$Name.exe") (Join-Path $Output "$Name.exe")
$Runtime = Join-Path $Output "dualcode-backend-runtime"
if (Test-Path $Runtime) { Remove-Item -Recurse -Force $Runtime }
Move-Item (Join-Path $Built "dualcode-backend-runtime") $Runtime
Remove-Item -Recurse -Force $Built
