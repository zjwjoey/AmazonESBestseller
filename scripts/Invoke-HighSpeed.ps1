[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe',
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
$taskWorkspace = Split-Path -Parent $PSScriptRoot
$taskSavedEnvironment = @{}
foreach ($taskVariable in @('PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONIOENCODING')) {
    $taskSavedEnvironment[$taskVariable] = [Environment]::GetEnvironmentVariable($taskVariable, 'Process')
}
try {
    $env:PYTHONPATH = Join-Path $taskWorkspace 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    & $Python -c 'import amazon_es_bestseller; print("V2 import:", amazon_es_bestseller.__file__)'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m amazon_es_bestseller.cli @Arguments
    exit $LASTEXITCODE
} finally {
    foreach ($taskVariable in $taskSavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($taskVariable, $taskSavedEnvironment[$taskVariable], 'Process')
    }
}
