$ErrorActionPreference = "Stop"

function Invoke-ProjectPython {
    param([string[]]$PythonArgs)

    & .\.venv\Scripts\python.exe @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the local Python virtual environment."
    }
}

Invoke-ProjectPython -PythonArgs @("-m", "pip", "install", "--upgrade", "pip")
Invoke-ProjectPython -PythonArgs @("-m", "pip", "install", "-r", "requirements.txt")

Write-Host "Setup complete. Start the app with .\run.ps1"
