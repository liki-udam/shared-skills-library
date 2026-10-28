# Shared Skills Library setup v1.0.1. Uses an existing Python interpreter only.
param([string]$DeviceName, [string]$SkillsRoot, [string]$LibraryRoot)
$ErrorActionPreference = 'Stop'
$taskBootstrap = Join-Path $PSScriptRoot 'bootstrap.py'
$taskCandidates = @()
$taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $taskBundledPython) { $taskCandidates += @{Executable=$taskBundledPython; Prefix=@()} }
foreach ($taskName in @('python','python3','py')) {
    $taskCommand = Get-Command $taskName -ErrorAction SilentlyContinue
    if ($taskCommand) { $taskCandidates += @{Executable=$taskCommand.Source; Prefix=$(if ($taskName -eq 'py') { @('-3') } else { @() })} }
}
foreach ($taskCandidate in $taskCandidates) {
    $taskExecutable = $taskCandidate.Executable
    $taskPrefix = @($taskCandidate.Prefix)
    & $taskExecutable @taskPrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 2)' 2>$null
    if ($LASTEXITCODE -eq 0) {
        $taskArguments = @($taskBootstrap)
        if ($LibraryRoot) { $taskArguments += @('--library-root', $LibraryRoot) }
        if ($DeviceName) { $taskArguments += @('--device-name', $DeviceName) }
        if ($SkillsRoot) { $taskArguments += @('--skills-root', $SkillsRoot) }
        & $taskExecutable @taskPrefix @taskArguments
        exit $LASTEXITCODE
    }
}
throw 'Python 3.9+ was not found. Ask Codex to locate its bundled Python and run bootstrap.py. Nothing was installed.'
