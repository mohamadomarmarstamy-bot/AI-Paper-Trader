$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Install Python 3.12 or newer from python.org, then run Setup.ps1 again.'
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw 'Install Ollama from https://ollama.com/download/windows, open it, then run Setup.ps1 again.'
}
& py -3 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
& .\.venv\Scripts\python.exe -m pip install 'requests>=2.32,<3'
if ($LASTEXITCODE -ne 0) { throw 'Could not install requests.' }
& ollama pull qwen2.5-coder:3b
if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Open Ollama and try again.' }
if (-not (Test-Path config.json)) { Copy-Item config.example.json config.json }
Write-Host 'Setup complete. First run .\Benchmark.ps1 to test the laptop without connecting to Railway.'
Write-Host 'Then follow README.md to configure the dedicated worker token before Start.ps1.'
