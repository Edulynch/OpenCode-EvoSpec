# Native R2-D2 evidence must be collected by the invoking terminal, not by
# the model. Every fixture is synthetic, disposable, and outside the repository.
$ErrorActionPreference = 'Stop'

$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$branchName = 'feat/e1-r2d2-parked-context-recovery'
$modelName = 'openai/gpt-6.1-sol#high'

function Get-FixtureInventory {
    param([Parameter(Mandatory)][string]$Root)

    # A stable read-only inventory exposes changes to bytes, timestamps, or
    # file membership without treating model prose as verification evidence.
    return @(
        Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
            Sort-Object FullName |
            ForEach-Object {
                [pscustomobject]@{
                    Path = $_.FullName.Substring($Root.Length)
                    Length = $_.Length
                    Modified = $_.LastWriteTimeUtc.Ticks
                    Hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                }
            }
    )
}

Push-Location $sourceRoot
try {
    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne $branchName) {
        throw "STOP: run only from the isolated $branchName worktree."
    }
    $dirty = @(& git status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
        throw 'STOP: source worktree must be clean.'
    }
    if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
        throw 'STOP: native OpenCode CLI is unavailable.'
    }

    # Fixture creation runs in the real PowerShell process. OpenCode never
    # needs permission to create an accepted test record or write evidence.
    $preparedText = (& python -m tests.r2d2_native_fixture --scenario both | Out-String)
    if ($LASTEXITCODE -ne 0) { throw 'STOP: fixture preparation failed.' }
    $prepared = $preparedText | ConvertFrom-Json
    if ($prepared.fixtures.Count -ne 2) { throw 'STOP: expected exactly two fixtures.' }
    $parent = [string]$prepared.root
    $evidenceRoot = Join-Path $parent 'evidence'
    New-Item -ItemType Directory -Path $evidenceRoot -ErrorAction Stop | Out-Null

    $jobs = @(
        [pscustomobject]@{ Name = 'parked-natural'; Scenario = 'parked'; Prompt = '¿dónde seguimos?' },
        [pscustomobject]@{ Name = 'parked-context'; Scenario = 'parked'; Prompt = '/evospec.context' },
        [pscustomobject]@{ Name = 'parked-status'; Scenario = 'parked'; Prompt = '/evospec.status' },
        [pscustomobject]@{ Name = 'ambiguous-natural'; Scenario = 'ambiguous'; Prompt = '¿dónde seguimos?' }
    )
    $results = @()
    foreach ($job in $jobs) {
        $fixture = @($prepared.fixtures | Where-Object { $_.scenario -eq $job.Scenario })[0]
        if ($null -eq $fixture -or -not $fixture.synthetic_only) {
            throw "STOP: no synthetic fixture for $($job.Scenario)."
        }
        $fixtureRoot = [string]$fixture.root
        if (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'EVOSPEC_TEST_FIXTURE_ONLY.txt'))) {
            throw "STOP: missing synthetic fixture marker for $($job.Scenario)."
        }
        $before = @(Get-FixtureInventory -Root $fixtureRoot)
        $beforeJson = ConvertTo-Json -InputObject $before -Depth 6 -Compress
        $outputPath = Join-Path $evidenceRoot "$($job.Name).ndjson"

        Push-Location $fixtureRoot
        try {
            # Four independent sessions; --auto handles only non-denied host
            # permissions. It never substitutes EvoSpec's real human decisions.
            $outputLines = @(& opencode run --auto --agent build --model $modelName --format json $job.Prompt 2>&1)
            $exitCode = $LASTEXITCODE
        }
        finally { Pop-Location }
        $outputLines | ForEach-Object { $_.ToString() } | Set-Content -LiteralPath $outputPath -Encoding utf8

        $after = @(Get-FixtureInventory -Root $fixtureRoot)
        $afterJson = ConvertTo-Json -InputObject $after -Depth 6 -Compress
        if ($beforeJson -cne $afterJson) {
            throw "STOP: fixture changed during $($job.Name). Evidence: $outputPath"
        }
        if ($exitCode -ne 0) {
            throw "BLOCKED: native host exited $exitCode for $($job.Name). Evidence: $outputPath"
        }
        $results += [pscustomobject]@{
            Scenario = $job.Name
            Root = $fixtureRoot
            Revision = $fixture.revision
            HostExitCode = $exitCode
            FilesUnchanged = $true
            Evidence = $outputPath
            NativeToolCalls = 'NEEDS_REVIEW'
            Outcome = 'NEEDS_EVIDENCE_REVIEW'
        }
    }

    $reportPath = Join-Path $evidenceRoot 'manifest.json'
    [pscustomobject]@{
        SourceBranch = $branch
        SourceHead = (& git rev-parse HEAD).Trim()
        Model = $modelName
        Agent = 'build'
        SyntheticOnly = $true
        HumanAcceptance = 'NOT_REQUESTED'
        Results = $results
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Output "EVIDENCE_READY: $reportPath"
    Write-Output 'All fixture inventories stayed unchanged; review NDJSON tool calls and answers before claiming PASS.'
}
finally { Pop-Location }
