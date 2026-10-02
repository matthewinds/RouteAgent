param([string]$Task='trip', [int]$TestNumber=1, [switch]$Check)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskBaseline = Join-Path $taskRoot 'baselines\mapagent'
$taskArgs = @('-X','utf8','scripts/run_mapagent.py','--task',$Task,'--test-number',$TestNumber)
if ($Check) { $taskArgs += '--check' }
Push-Location -LiteralPath $taskBaseline
try { & (Join-Path $taskRoot '.venv\Scripts\python.exe') @taskArgs; $taskExit = $LASTEXITCODE }
finally { Pop-Location }
exit $taskExit
