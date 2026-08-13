param(
    [int]$Rate = 100,
    [string]$Duration = "30s",
    [string]$OutputDirectory = "../../outputs/phase22-results"
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command vegeta -ErrorAction SilentlyContinue)) {
    throw "vegeta is not installed or not available on PATH"
}

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$targets = Join-Path $scriptDirectory "vegeta-targets.txt"
$outputs = Join-Path $scriptDirectory $OutputDirectory
New-Item -ItemType Directory -Force -Path $outputs | Out-Null
$result = Join-Path $outputs "vegeta-results.bin"
$report = Join-Path $outputs "vegeta-report.json"

Get-Content $targets | vegeta attack -rate "$Rate/s" -duration $Duration -output $result
vegeta report -type json -output $report $result
Write-Output $report
