param(
    [ValidateSet("py-spy", "scalene", "memray")]
    [string]$Tool = "py-spy",

    [int]$Pid = 0,
    [string]$ProcessName = "python",
    [int]$DurationSec = 60,
    [string]$OutputDir = "runtime/profiles",
    [string]$Command = ""
)

$ErrorActionPreference = "Stop"

function Resolve-ProfilePid {
    param(
        [int]$RequestedPid,
        [string]$RequestedName
    )
    if ($RequestedPid -gt 0) {
        return $RequestedPid
    }
    $candidates = Get-Process -Name $RequestedName -ErrorAction SilentlyContinue |
        Sort-Object StartTime -Descending
    if (-not $candidates) {
        throw "No process found for name '$RequestedName'. Pass -Pid explicitly."
    }
    return [int]$candidates[0].Id
}

function Require-Command {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Required profiler '$Name' was not found in PATH. Install it in the active environment first."
    }
    return $cmd.Source
}

function Split-CommandArgs {
    param([string]$RawCommand)
    $items = @()
    foreach ($item in ($RawCommand -split "\s+")) {
        if ($item.Trim()) {
            $items += $item.Trim()
        }
    }
    return $items
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

if ($Tool -eq "py-spy") {
    $targetPid = Resolve-ProfilePid -RequestedPid $Pid -RequestedName $ProcessName
    Require-Command "py-spy" | Out-Null
    $output = Join-Path $OutputDir "py-spy_${targetPid}_${stamp}.svg"
    Write-Host "Recording py-spy flamegraph for PID $targetPid -> $output"
    py-spy record --pid $targetPid --duration $DurationSec --output $output
    Write-Host $output
    exit 0
}

if ($Tool -eq "scalene") {
    if (-not $Command) {
        throw "Scalene needs -Command, for example: -Command 'apps/elara_core/main.py'"
    }
    Require-Command "scalene" | Out-Null
    $output = Join-Path $OutputDir "scalene_${stamp}.html"
    $commandArgs = Split-CommandArgs -RawCommand $Command
    Write-Host "Running scalene command profile -> $output"
    scalene --html --outfile $output @commandArgs
    Write-Host $output
    exit 0
}

if ($Tool -eq "memray") {
    if (-not $Command) {
        throw "Memray needs -Command, for example: -Command 'apps/elara_trader/main.py'"
    }
    Require-Command "memray" | Out-Null
    $bin = Join-Path $OutputDir "memray_${stamp}.bin"
    $html = Join-Path $OutputDir "memray_${stamp}.html"
    $commandArgs = Split-CommandArgs -RawCommand $Command
    Write-Host "Recording memray allocation profile -> $bin"
    memray run -o $bin @commandArgs
    memray flamegraph -o $html $bin
    Write-Host $html
    exit 0
}
