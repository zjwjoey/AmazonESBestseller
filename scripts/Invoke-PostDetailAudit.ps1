param(
    [string]$Python = 'python',
    [ValidateSet('quality-audit', 'audit-fields', 'source-evidence-audit')]
    [string]$Command = 'quality-audit',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
$auditRepository = Split-Path -Parent $PSScriptRoot
$auditSource = Join-Path $auditRepository 'src'
$auditPreviousPath = $env:PYTHONPATH
$auditPreviousEncoding = $env:PYTHONIOENCODING
$auditPreviousBytecode = $env:PYTHONDONTWRITEBYTECODE
try {
    $env:PYTHONPATH = $auditSource
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $Python -c "import pathlib,sys,amazon_es_bestseller; p=pathlib.Path(amazon_es_bestseller.__file__).resolve(); assert p.is_relative_to(pathlib.Path(sys.argv[1]).resolve()),p; print('Audit module:',p)" $auditSource
    if ($LASTEXITCODE -ne 0) { throw 'Audit Python import verification failed.' }
    & $Python -m amazon_es_bestseller.cli --offline $Command @Arguments
    exit $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $auditPreviousPath
    $env:PYTHONIOENCODING = $auditPreviousEncoding
    $env:PYTHONDONTWRITEBYTECODE = $auditPreviousBytecode
}
