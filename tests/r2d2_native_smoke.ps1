# Native R2-D2 evidence must be collected by the invoking terminal, not by
# the model. Every fixture is synthetic, disposable, and outside the repository.
param([switch]$IncludeRemainingScenarios)

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

function Get-FreeLoopbackPort {
    # Ask the OS for a temporary port so the private host never binds to a
    # fixed or externally accessible address. A later collision fails closed.
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally { $listener.Stop() }
}

function Assert-PrivateHostReady {
    param(
        [Parameter(Mandatory)][string]$FixtureRoot,
        [Parameter(Mandatory)][string]$ServerUrl,
        [Parameter(Mandatory)][System.Diagnostics.Process]$ServerProcess,
        [Parameter(Mandatory)][hashtable]$Evidence
    )

    # No model request is allowed until the private host reports the exact
    # fixture location and an active EvoSpec plugin. Plugin setup itself
    # verifies registration of evospec_context in the native tool registry.
    $location = $null
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        if ($ServerProcess.HasExited) {
            throw "STOP: private OpenCode server exited before location discovery (exit $($ServerProcess.ExitCode))."
        }
        try {
            $location = Invoke-RestMethod -Method Get -Uri "$ServerUrl/api/location" -TimeoutSec 2
            break
        }
        catch { Start-Sleep -Milliseconds 250 }
    }
    if ($null -eq $location) { throw 'STOP: private OpenCode server did not answer /api/location.' }

    $reportedRoot = if ($null -ne $location.location -and $location.location.directory) {
        [string]$location.location.directory
    } elseif ($null -ne $location.data -and $location.data.directory) {
        [string]$location.data.directory
    } else { [string]$location.directory }
    $Evidence.ObservedLocation = $reportedRoot
    $expectedRoot = [System.IO.Path]::GetFullPath($FixtureRoot).TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($reportedRoot)) {
        throw 'STOP: /api/location did not report a directory.'
    }
    $actualRoot = [System.IO.Path]::GetFullPath($reportedRoot).TrimEnd('\', '/')
    if (-not [string]::Equals($expectedRoot, $actualRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "STOP: private host location mismatch: $reportedRoot."
    }
    $Evidence.LocationVerified = $true

    # OpenCode V2 activates local plugins asynchronously. Polling this API
    # cannot consume provider tokens or create agent sessions.
    $encodedRoot = [uri]::EscapeDataString($FixtureRoot)
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        if ($ServerProcess.HasExited) { throw 'STOP: private OpenCode server stopped during plugin discovery.' }
        try {
            $reply = Invoke-RestMethod -Method Get -Uri "$ServerUrl/api/plugin?location%5Bdirectory%5D=$encodedRoot" -TimeoutSec 3
            $plugin = @($reply.data | Where-Object { $_.id -eq 'evospec' } | Select-Object -First 1)
            if ($plugin.Count -gt 0) {
                $state = [string]$plugin[0].state.status
                $Evidence.PluginState = $state
                if ($state -eq 'active') {
                    $Evidence.PluginVerified = $true
                    return
                }
                if ($state -eq 'failed') { throw 'STOP: EvoSpec plugin activation failed in the private host.' }
            }
        }
        catch {
            if ($_.Exception.Message -like 'STOP:*') { throw }
            # An initial 503 or connection delay is expected while plugins load.
        }
        Start-Sleep -Milliseconds 250
    }
    throw "STOP: EvoSpec plugin did not become active (last state: $($Evidence.PluginState))."
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
    if ($LASTEXITCODE -ne 0 -or $runHelp -notmatch '--server') {
        throw 'STOP: this OpenCode run command does not advertise --server.'
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

    $jobs = @([pscustomobject]@{ Name = 'parked-natural'; Scenario = 'parked'; Prompt = '¿dónde seguimos?' })
    if ($IncludeRemainingScenarios) {
        # The other cases are opt-in: a successful first preflight must not
        # silently spend tokens on unrelated native sessions.
        $jobs += @(
            [pscustomobject]@{ Name = 'parked-context'; Scenario = 'parked'; Prompt = '/evospec.context' },
            [pscustomobject]@{ Name = 'parked-status'; Scenario = 'parked'; Prompt = '/evospec.status' },
            [pscustomobject]@{ Name = 'ambiguous-natural'; Scenario = 'ambiguous'; Prompt = '¿dónde seguimos?' }
        )
    }
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
        $preflightPath = Join-Path $evidenceRoot "preflight-$($job.Name).json"
        $preflight = @{
            Scenario = $job.Name
            ExpectedLocation = $fixtureRoot
            ObservedLocation = $null
            LocationVerified = $false
            PluginState = 'NOT_DISCOVERED'
            PluginVerified = $false
            ModelCallsIssuedByRunnerBeforePreflight = 0
        }
        $serverProcess = $null
        try {
            $port = Get-FreeLoopbackPort
            $serverUrl = "http://127.0.0.1:$port"
            # This private server remains alive for the exact session whose
            # plugin and location we inspect. No shared service is reused.
            # An encoded command preserves Windows paths and non-ASCII text;
            # retaining its process handle permits exact process-tree cleanup.
            $escapedRoot = $fixtureRoot.Replace("'", "''")
            $serveCommand = "Set-Location -LiteralPath '$escapedRoot'; & opencode serve --hostname 127.0.0.1 --port $port"
            $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($serveCommand))
            $serverProcess = Start-Process -FilePath (Get-Command pwsh -CommandType Application).Source `
                -ArgumentList @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encodedCommand) `
                -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $evidenceRoot "server-$($job.Name).stdout.log") `
                -RedirectStandardError (Join-Path $evidenceRoot "server-$($job.Name).stderr.log")
            Assert-PrivateHostReady -FixtureRoot $fixtureRoot -ServerUrl $serverUrl -ServerProcess $serverProcess -Evidence $preflight

            Push-Location $fixtureRoot
            try {
                if ((Get-Location).ProviderPath -ne $fixtureRoot) {
                    throw "STOP: shell did not enter the isolated fixture $fixtureRoot."
                }
                # The model connects to the same preflighted private server;
                # --auto handles only non-denied host permissions.
                $outputLines = @(& opencode run --server $serverUrl --auto --agent build --model $modelName --format json $job.Prompt 2>&1)
                $exitCode = $LASTEXITCODE
            }
            finally { Pop-Location }
        }
        catch {
            throw "$($_.Exception.Message) Preflight evidence: $preflightPath"
        }
        finally {
            try {
                # Keep a small, credential-free record even if the host blocks.
                $preflight | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $preflightPath -Encoding utf8
            }
            finally {
                # Cleanup must run even when evidence cannot be written.
                if ($null -ne $serverProcess) {
                    try {
                        if (-not $serverProcess.HasExited) {
                            # Kill the exact private server process tree; leave
                            # shared or unrelated OpenCode servers untouched.
                            $serverProcess.Kill($true)
                            [void]$serverProcess.WaitForExit(5000)
                        }
                    }
                    finally { $serverProcess.Dispose() }
                }
            }
        }
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
        PrivateServerPreflight = 'PASSED'
        HumanAcceptance = 'NOT_REQUESTED'
        Results = $results
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Output "EVIDENCE_READY: $reportPath"
    Write-Output 'Canonical context calls and unchanged inventories verified; review agent answers before claiming PASS.'
}
finally { Pop-Location }
