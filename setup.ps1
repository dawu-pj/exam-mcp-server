# One-click setup for crawler-mvp on a fresh Windows machine.
# Pure ASCII on purpose: PowerShell 5.1 parses .ps1 with the system ANSI code page,
# and non-ASCII characters can break syntax on Chinese Windows.
# Run: powershell -ExecutionPolicy Bypass -File setup.ps1
# What it does: ensure uv -> ensure Python 3.12 -> create .venv -> install pinned deps -> self-test.

# NOTE: keep ErrorActionPreference = Continue. PowerShell 5.1 treats native tools'
# stderr progress (uv prints "Checked N packages" to stderr) as terminating errors
# under "Stop". We check critical steps explicitly instead.
$ErrorActionPreference = "Continue"
$Proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$UvExe = "C:\software\uv\bin\uv.exe"
$PyDir = "C:\software\uv\python"
$PyExe = "$PyDir\cpython-3.12.16-windows-x86_64-none\python.exe"

Write-Host "=== crawler-mvp setup ==="
Write-Host "Project: $Proj"

# 1. Ensure uv
if (-not (Test-Path $UvExe)) {
    Write-Host "[1/5] uv not found. Installing to C:\software\uv\bin ..."
    $env:UV_INSTALL_DIR = "C:\software\uv\bin"
    $tmp = Join-Path $env:TEMP "uv_install_crawler.ps1"
    Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" -TimeoutSec 60 | Set-Content -Path $tmp -Encoding UTF8
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $tmp
    if (-not (Test-Path $UvExe)) { throw "uv install failed: $UvExe not found" }
} else {
    Write-Host "[1/5] uv found: $UvExe"
}
& $UvExe --version

# 2. Ensure Python 3.12 (uv-managed, does not touch system PATH)
if (-not (Test-Path $PyExe)) {
    Write-Host "[2/5] Python 3.12 not found. Installing via uv ..."
    $env:UV_PYTHON_INSTALL_DIR = $PyDir
    & $UvExe python install 3.12
    if (-not (Test-Path $PyExe)) {
        Write-Host "  Exact build path differs; locating any cpython-3.12 under $PyDir"
        $found = Get-ChildItem -Path $PyDir -Filter "python.exe" -Recurse -File -ErrorAction SilentlyContinue |
                 Where-Object { $_.FullName -match "cpython-3\.12" } | Select-Object -First 1
        if ($found) { $PyExe = $found.FullName } else { throw "Python 3.12 install failed" }
    }
} else {
    Write-Host "[2/5] Python 3.12 found: $PyExe"
}

# 3. Create venv
$VenvPy = "$Proj\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "[3/5] Creating .venv ..."
    Push-Location $Proj
    & $UvExe venv --python $PyExe .venv
    Pop-Location
} else {
    Write-Host "[3/5] .venv exists, skipping"
}

# 4. Install pinned dependencies from requirements.txt (Aliyun mirror for China)
Write-Host "[4/5] Installing pinned dependencies ..."
Push-Location $Proj
& $UvExe pip install --python ".venv\Scripts\python.exe" `
    --index-url "https://mirrors.aliyun.com/pypi/simple/" -r requirements.txt
Pop-Location

# 5. Self-test
Write-Host "[5/5] Running self-test ..."
Push-Location $Proj
& ".venv\Scripts\python.exe" src/selftest.py
& ".venv\Scripts\python.exe" tests/test_mcp.py
Pop-Location

Write-Host ""
Write-Host "=== Setup complete ==="
Write-Host "Next: .\.venv\Scripts\python.exe quick.py ask `"CET exam registration`""
Write-Host "     .\.venv\Scripts\python.exe quick.py crawl   (collect 5 sites)"
Write-Host "     .\.venv\Scripts\python.exe quick.py serve --http --port 8765   (MCP server)"
