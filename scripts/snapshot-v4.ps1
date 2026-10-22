[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$PythonExe,
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$SnapshotArguments
)
$ErrorActionPreference = 'Stop'
$snapshotRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonExe) {
    $snapshotBundledPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (Test-Path -LiteralPath $snapshotBundledPython) {
        $PythonExe = $snapshotBundledPython
    } else {
        $PythonExe = (Get-Command python.exe -ErrorAction Stop).Source
    }
}
$snapshotPreviousPythonPath = $env:PYTHONPATH
$snapshotPreviousUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONPATH = Join-Path $snapshotRoot 'src'
    $env:PYTHONUTF8 = '1'
    Write-Host "Snapshot code: $snapshotRoot"
    & $PythonExe -m amazon_es_bestseller.highspeed.v4_cli @SnapshotArguments
    $snapshotExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $snapshotPreviousPythonPath
    $env:PYTHONUTF8 = $snapshotPreviousUtf8
}
exit $snapshotExitCode
