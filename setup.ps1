# Thin wrapper around scripts/setup.py for native Windows users without
# `make`. See that file for the actual hardware-detection/install logic.
# Usage: .\setup.ps1 [-- --core-only|--dry-run|--apply-model-config|...]

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$PythonCmd = $null
foreach ($candidate in @("python", "py")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        $PythonCmd = $candidate
        break
    }
}

if (-not $PythonCmd) {
    Write-Error "No python interpreter found on PATH (tried 'python', 'py'). Install Python 3 first."
    exit 1
}

& $PythonCmd (Join-Path $PSScriptRoot "scripts\setup.py") @Args
exit $LASTEXITCODE
