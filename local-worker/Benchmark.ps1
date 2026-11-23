$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& .\.venv\Scripts\python.exe worker.py --benchmark
if ($LASTEXITCODE -ne 0) { throw 'Benchmark failed. Check Ollama is open and the model is downloaded.' }
Write-Host 'Benchmark and generated strategy saved in worker-state.json. No Railway strategy was submitted.'
