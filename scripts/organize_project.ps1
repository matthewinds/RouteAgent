$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
if ($taskRoot -ne 'F:\develop\projects\MapAgents') { throw 'Unexpected workspace root' }
if (Test-Path -LiteralPath (Join-Path $taskRoot 'docs\ARCHIVE_INDEX.md')) { throw 'Organization already completed; refusing to archive the new FYP project' }
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$taskArchive = Join-Path 'F:\develop\archives\MapAgents' $taskStamp
if (Test-Path -LiteralPath $taskArchive) { throw 'Archive already exists' }
$taskOldNames = @('README.md','REPRODUCTION.md','requirements.txt','runtime_config.py','utilities.py','parallel_function_implementation.py','answer_extraction.py','.env.example','.gitignore','.DS_Store','__pycache__','datasets_dir','datasets_original','datasets_adapted','new','new_gpt35','new_gpt35_improved','new_gpt35_reextracted','new_gpt35_selected','outputs','src','tests','textual_baseline_gpt35','textual_mapagent_run','textual_mapagent_context_run','visual_mapagent_half_gpt4o','visual_mapagent_half_gpt4o_v2','tmp')
$taskOldScriptNames = @('audit_data.py','check_services.py','download_visual_original.py','prepare_mapeval_textual_mapagent.py','reextract_results.py','rescore_results.py','run_mapagent.py','run_mapeval_textual_baseline.py','run_mapeval_textual_mapagent_context.py','run_mapeval_visual_half.py','verify_original_datasets.py','__pycache__')
$taskSources = @($taskOldNames | ForEach-Object { Join-Path $taskRoot $_ }) + @($taskOldScriptNames | ForEach-Object { Join-Path (Join-Path $taskRoot 'scripts') $_ })
$taskSources = @($taskSources | Where-Object { Test-Path -LiteralPath $_ })
foreach ($taskSource in $taskSources) {
    $taskResolved = (Resolve-Path -LiteralPath $taskSource).Path
    if (-not $taskResolved.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Source outside workspace' }
    $taskItem = Get-Item -LiteralPath $taskResolved -Force
    if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing link: $taskResolved" }
    if ($taskItem.PSIsContainer) {
        $taskLinks = @(Get-ChildItem -LiteralPath $taskResolved -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
        if ($taskLinks.Count) { throw 'Refusing directory containing links' }
    }
}
New-Item -ItemType Directory -Path $taskArchive | Out-Null
$taskRecords = [Collections.Generic.List[object]]::new()
foreach ($taskSource in $taskSources) {
    $taskRelative = $taskSource.Substring($taskRoot.Length + 1)
    $taskDestination = Join-Path $taskArchive $taskRelative
    New-Item -ItemType Directory -Path (Split-Path $taskDestination) -Force | Out-Null
    Copy-Item -LiteralPath $taskSource -Destination $taskDestination -Recurse -Force
    $taskSourceItem = Get-Item -LiteralPath $taskSource -Force
    $taskFiles = if ($taskSourceItem.PSIsContainer) { @(Get-ChildItem -LiteralPath $taskSource -Recurse -File -Force) } else { @($taskSourceItem) }
    foreach ($taskFile in $taskFiles) {
        $taskRelFile = $taskFile.FullName.Substring($taskRoot.Length + 1)
        $taskCopy = Join-Path $taskArchive $taskRelFile
        $taskHash = (Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash
        $taskCopyItem = Get-Item -LiteralPath $taskCopy
        if ($taskCopyItem.Length -ne $taskFile.Length -or (Get-FileHash -LiteralPath $taskCopy -Algorithm SHA256).Hash -ne $taskHash) { throw "Backup mismatch: $taskRelFile" }
        $taskRecords.Add([pscustomobject]@{path=$taskRelFile;bytes=$taskFile.Length;sha256=$taskHash})
    }
}
$taskManifest = [pscustomobject]@{source=$taskRoot;archive=$taskArchive;created=(Get-Date -Format o);git_head=(git -C $taskRoot rev-parse HEAD);files=$taskRecords;verified=$true}
$taskManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $taskArchive 'archive_manifest.json') -Encoding utf8
$taskBaseline = Join-Path $taskRoot 'baselines\mapagent'
New-Item -ItemType Directory -Path $taskBaseline -Force | Out-Null
$taskKeep = @('README.md','REPRODUCTION.md','requirements.txt','runtime_config.py','utilities.py','parallel_function_implementation.py','answer_extraction.py','.env.example','src\model.py','src\run.py','src\demos','datasets_dir\txt_data','scripts\run_mapagent.py','scripts\check_services.py','scripts\audit_data.py','tests\test_runtime.py','tests\test_evaluation.py','tests\test_google_maps_fallback.py','tests\test_answer_extraction.py')
foreach ($taskKeepPath in $taskKeep) {
    $taskBaseDestination = Join-Path $taskBaseline $taskKeepPath
    New-Item -ItemType Directory -Path (Split-Path $taskBaseDestination) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $taskArchive $taskKeepPath) -Destination $taskBaseDestination -Recurse -Force
}
# Configuration path only: baseline prompts, arithmetic, and inference are retained.
$taskRuntime = Join-Path $taskBaseline 'runtime_config.py'
$taskRuntimeContent = Get-Content -LiteralPath $taskRuntime -Raw
$taskRuntimeContent = $taskRuntimeContent.Replace('load_dotenv(ROOT / ".env", override=False)', 'load_dotenv(ROOT.parents[1] / ".env", override=False)')
Set-Content -LiteralPath $taskRuntime -Value $taskRuntimeContent -Encoding utf8
Push-Location -LiteralPath $taskBaseline
try {
    & (Join-Path $taskRoot '.venv\Scripts\python.exe') -X utf8 -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Baseline validation failed; originals remain in place' }
} finally { Pop-Location }
foreach ($taskSource in $taskSources) {
    $taskResolved = (Resolve-Path -LiteralPath $taskSource).Path
    if (-not $taskResolved.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Deletion outside workspace' }
    if ($taskResolved -eq (Join-Path $taskRoot '.git') -or $taskResolved -eq (Join-Path $taskRoot '.env')) { throw 'Protected target' }
    Remove-Item -LiteralPath $taskResolved -Recurse -Force
}
$taskIndex = "# 历史项目归档`n`n归档目录：$taskArchive`n`n校验清单：$taskArchive\archive_manifest.json`n`n已逐文件核对大小和 SHA-256，共 $($taskRecords.Count) 个文件。根目录凭据、Git 历史和原环境未移动。`n`n独立基线：baselines/mapagent。仅调整配置文件读取位置到项目根目录；提示词、推理和计算逻辑保留。离线基线测试通过后才删除旧路径。`n`n恢复：从归档按 manifest 中相对路径复制回目标目录；先校验哈希，再使用原环境运行。归档未包含凭据和虚拟环境。`n"
$taskIndex | Set-Content -LiteralPath (Join-Path $taskRoot 'docs\ARCHIVE_INDEX.md') -Encoding utf8
Write-Output "Verified archive: $taskArchive; files: $($taskRecords.Count); baseline tests passed; cleanup complete."
