param(
    [string]$Python = 'python',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
$snapshotRepository = Split-Path -Parent $PSScriptRoot
$snapshotSource = Join-Path $snapshotRepository 'src'
$previousPythonPath = $env:PYTHONPATH
$previousPythonEncoding = $env:PYTHONIOENCODING
try {
    $env:PYTHONPATH = $snapshotSource
    $env:PYTHONIOENCODING = 'utf-8'
    & $Python -c "import pathlib,amazon_es_bestseller; print('Module:',pathlib.Path(amazon_es_bestseller.__file__).resolve())"
    if ($LASTEXITCODE -ne 0) { throw 'Python import verification failed.' }
    & $Python -m amazon_es_bestseller.cli multisource @Arguments
    exit $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONIOENCODING = $previousPythonEncoding
}
