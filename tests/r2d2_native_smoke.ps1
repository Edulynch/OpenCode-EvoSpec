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

function Get-CanonicalContextCalls {
    param([Parameter(Mandatory)][string]$EvidencePath)

    # Read the actual native events. A model merely mentioning a tool or
    # searching for its name is never evidence that the tool was invoked.
    $events = @(Get-Content -LiteralPath $EvidencePath -Encoding utf8 |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
    return @($events | Where-Object {
        if ($_.type -ne 'tool_use' -or $_.part.state.status -ne 'completed') {
            return $false
        }
        if ($_.part.tool -eq 'evospec_context') { return $true }
        if ($_.part.tool -ne 'execute') { return $false }
        return [string]$_.part.state.input.code -match 'tools\.evospec_context\s*\('
    })
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
    # A private server is essential: the shared server previously executed
    # these prompts against master instead of the temporary fixture.
    $runHelp = (& opencode run --help | Out-String)
    if ($LASTEXITCODE -ne 0 -or $runHelp -notmatch '--standalone') {
        throw 'STOP: this OpenCode run command does not advertise --standalone.'
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
        if (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot '.opencode/plugins/evospec/index.ts'))) {
            throw "STOP: missing EvoSpec local plugin in $fixtureRoot."
        }
        $before = @(Get-FixtureInventory -Root $fixtureRoot)
        $beforeJson = ConvertTo-Json -InputObject $before -Depth 6 -Compress
        $outputPath = Join-Path $evidenceRoot "$($job.Name).ndjson"

        Push-Location $fixtureRoot
        try {
            if ((Get-Location).ProviderPath -ne $fixtureRoot) {
                throw "STOP: shell did not enter the isolated fixture $fixtureRoot."
            }
            # Four independent sessions; --auto handles only non-denied host
            # permissions. --standalone prevents a shared background server
            # from silently routing the model into an unrelated worktree.
            $outputLines = @(& opencode run --standalone --auto --agent build --model $modelName --format json $job.Prompt 2>&1)
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
        $contextCalls = @(Get-CanonicalContextCalls -EvidencePath $outputPath)
        $requiredTitle = if ($job.Scenario -eq 'parked') { 'Fixture temporal R2-D2' } else { 'Another synthetic work item' }
        $matchedContext = @($contextCalls | Where-Object {
            [string]$_.part.state.output -match [regex]::Escape($requiredTitle)
        })
        if ($matchedContext.Count -eq 0) {
            throw "STOP: $($job.Name) did not call evospec_context for its fixture. No more model sessions. Evidence: $outputPath"
        }
        $ambiguousContexts = @($matchedContext | Where-Object {
            [string]$_.part.state.output -match 'AMBIGUOUS'
        })
        if ($job.Scenario -eq 'ambiguous' -and $ambiguousContexts.Count -eq 0) {
            throw "STOP: the ambiguous native context was not observed. Evidence: $outputPath"
        }
        $results += [pscustomobject]@{
            Scenario = $job.Name
            Root = $fixtureRoot
            Revision = $fixture.revision
            HostExitCode = $exitCode
            FilesUnchanged = $true
            Evidence = $outputPath
            CanonicalContextCalls = $contextCalls.Count
            Outcome = 'NEEDS_ANSWER_REVIEW'
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
    Write-Output 'Canonical context calls and unchanged inventories verified; review agent answers before claiming PASS.'
}
finally { Pop-Location }
