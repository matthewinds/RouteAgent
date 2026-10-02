param([int]$Port=8502)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $taskRoot
try { & (Join-Path $taskRoot '.venv-fyp\Scripts\python.exe') -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port $Port --browser.gatherUsageStats false }
finally { Pop-Location }
