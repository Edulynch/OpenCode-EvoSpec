# Native R2-D2 evidence must be collected by the invoking terminal, not by
# the model. Every fixture is synthetic, disposable, and outside the repository.
param(
    [switch]$IncludeRemainingScenarios,
    [string]$ApprovedModel = '',
    [string]$ReplayEvidenceRoot = ''
)

$ErrorActionPreference = 'Stop'

$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$branchName = 'feat/e1-r2d2-parked-context-recovery'
$authorizedModel = 'openai/gpt-6.1-sol#high'

# This test-only exception is intentionally explicit. A private server does
# not inherit a model selected in another session, and must never fall back
# to OpenCode's free tier or silently switch to another paid model.
if ([string]::IsNullOrWhiteSpace($ReplayEvidenceRoot) -and $ApprovedModel -cne $authorizedModel) {
    throw "STOP: pass -ApprovedModel '$authorizedModel' for this authorized native qualification."
}

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

function Test-CanonicalFixtureContext {
    param(
        [Parameter(Mandatory)][string]$Output,
        [Parameter(Mandatory)][ValidateSet('parked', 'ambiguous')][string]$Scenario
    )

    # Core only exposes an accepted title for the first work item. The second
    # ambiguous candidate has no acceptance yet, so its title is not evidence.
    try { $response = $Output | ConvertFrom-Json -ErrorAction Stop }
    catch { return $false }
    if ($null -ne $response.error -or $null -eq $response.context) { return $false }
    $context = $response.context
    if ($null -ne $context.accepted_slice -or $null -ne $context.work_queue.active) { return $false }

    $items = @($context.work_queue.all)
    $accepted = @($items | Where-Object {
        $null -ne $_.accepted_slice -and
        $_.accepted_slice.feature_title -ceq 'Fixture temporal R2-D2'
    })
    if ($accepted.Count -ne 1) { return $false }

    if ($Scenario -eq 'parked') {
        return ($items.Count -eq 1 -and
            $context.work_queue.next_code -ceq 'NONE' -and
            [int]$context.work_queue.next_candidates_total -eq 0)
    }

    $candidates = @($context.work_queue.next_candidates)
    $candidateIds = @($candidates | ForEach-Object { [string]$_ })
    $itemIds = @($items | ForEach-Object { [string]$_.id })
    # Two distinct, real queue IDs establish ambiguity without inventing
    # metadata for an unaccepted item or trusting the agent's prose.
    return ($items.Count -eq 2 -and
        $context.work_queue.next_code -ceq 'AMBIGUOUS' -and
        [int]$context.work_queue.next_candidates_total -eq 2 -and
        $candidateIds.Count -eq 2 -and
        @($candidateIds | Sort-Object -Unique).Count -eq 2 -and
        @($candidateIds | Where-Object { $itemIds -ccontains $_ }).Count -eq 2 -and
        @($items | Where-Object { $null -eq $_.accepted_slice }).Count -eq 1)
}

function Assert-RecordedFixtureEvidence {
    param(
        [Parameter(Mandatory)][string]$EvidencePath,
        [Parameter(Mandatory)][string]$PreflightPath,
        [Parameter(Mandatory)][ValidateSet('parked', 'ambiguous')][string]$Scenario,
        [Parameter(Mandatory)][string]$Name
    )

    # Replay reads previously collected evidence only. It does not start
    # OpenCode, create fixtures, re-run inference, or update the manifest.
    if (-not (Test-Path -LiteralPath $EvidencePath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $PreflightPath -PathType Leaf)) {
        throw "STOP: missing native replay evidence for $Name."
    }
    $preflight = Get-Content -LiteralPath $PreflightPath -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    if ($preflight.Scenario -cne $Name -or $preflight.PluginState -cne 'active' -or
        $preflight.ApprovedRunModel -cne $authorizedModel -or
        [int]$preflight.ModelCallsIssuedByRunnerBeforePreflight -ne 0 -or
        [string]::IsNullOrWhiteSpace([string]$preflight.SessionID)) {
        throw "STOP: unverified recorded preflight for $Name."
    }
    foreach ($flag in @('LocationVerified', 'PluginVerified', 'ModelCatalogVerified',
        'PythonCommandVerified', 'PythonImportVerified', 'PythonContextVerified', 'SessionVerified')) {
        if ($preflight.$flag -cne $true) { throw "STOP: $flag was not verified for $Name." }
    }
    if ([string]::IsNullOrWhiteSpace([string]$preflight.ExpectedLocation) -or
        $preflight.ExpectedLocation -cne $preflight.ObservedLocation -or
        $preflight.ExpectedLocation -cne $preflight.SessionLocation) {
        throw "STOP: recorded native paths differ for $Name."
    }
    $events = @(Get-Content -LiteralPath $EvidencePath -Encoding utf8 |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
    $sessionIds = @($events | Where-Object { $_.type -eq 'step_start' } |
        ForEach-Object { $_.sessionID } | Sort-Object -Unique)
    if ($sessionIds.Count -ne 1 -or $sessionIds[0] -cne $preflight.SessionID) {
        throw "STOP: recorded native session differs from preflight for $Name."
    }
    $calls = @(Get-CanonicalContextCalls -EvidencePath $EvidencePath)
    $matches = @($calls | Where-Object {
        Test-CanonicalFixtureContext -Output ([string]$_.part.state.output) -Scenario $Scenario
    })
    if ($matches.Count -eq 0) {
        throw "STOP: no canonical $Scenario fixture context in recorded events for $Name."
    }
    Write-Output "REPLAY_VALIDATED: $Name (session $($preflight.SessionID))"
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
        [Parameter(Mandatory)][string]$Authorization,
        [Parameter(Mandatory)][hashtable]$Evidence
    )

    # No model request is allowed until the private host reports the exact
    # fixture location and an active EvoSpec plugin. Plugin setup itself
    # verifies registration of evospec_context in the native tool registry.
    $headers = @{ Authorization = $Authorization }
    $location = $null
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        if ($ServerProcess.HasExited) {
            throw "STOP: private OpenCode server exited before location discovery (exit $($ServerProcess.ExitCode))."
        }
        try {
            $location = Invoke-RestMethod -Method Get -Uri "$ServerUrl/api/location" -Headers $headers -TimeoutSec 2
            break
        }
        catch {
            if ([int]$_.Exception.Response.StatusCode -eq 401) {
                throw 'STOP: private OpenCode API rejected preflight authentication (HTTP 401).'
            }
            Start-Sleep -Milliseconds 250
        }
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
            $reply = Invoke-RestMethod -Method Get -Uri "$ServerUrl/api/plugin?location%5Bdirectory%5D=$encodedRoot" -Headers $headers -TimeoutSec 3
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
            if ([int]$_.Exception.Response.StatusCode -eq 401) {
                throw 'STOP: private OpenCode API rejected plugin authentication (HTTP 401).'
            }
            # An initial 503 or connection delay is expected while plugins load.
        }
        Start-Sleep -Milliseconds 250
    }
    throw "STOP: EvoSpec plugin did not become active (last state: $($Evidence.PluginState))."
}

function Assert-ApprovedModelReady {
    param(
        [Parameter(Mandatory)][string]$FixtureRoot,
        [Parameter(Mandatory)][string]$ServerUrl,
        [Parameter(Mandatory)][string]$Authorization,
        [Parameter(Mandatory)][string]$ApprovedModel,
        [Parameter(Mandatory)][hashtable]$Evidence
    )

    # Inspect only the isolated host's model catalog. Listing models and the
    # default does not start an agent or consume inference tokens.
    $headers = @{ Authorization = $Authorization }
    $encodedRoot = [uri]::EscapeDataString($FixtureRoot)
    try {
        $catalog = Invoke-RestMethod -Method Get -Uri "$ServerUrl/api/model?location%5Bdirectory%5D=$encodedRoot" `
            -Headers $headers -TimeoutSec 10
        $default = Invoke-RestMethod -Method Get -Uri "$ServerUrl/api/model/default?location%5Bdirectory%5D=$encodedRoot" `
            -Headers $headers -TimeoutSec 10
    }
    catch {
        throw "STOP: isolated OpenCode host could not attest its model catalog: $($_.Exception.Message)"
    }

    $defaultModel = $default.data
    if ($null -ne $defaultModel -and $defaultModel.providerID -and $defaultModel.id) {
        $Evidence.UnselectedHostDefault = "$($defaultModel.providerID)/$($defaultModel.id)"
    }
    else {
        $Evidence.UnselectedHostDefault = 'NOT_ATTESTED'
    }

    $available = @($catalog.data | Where-Object {
        $_.providerID -ceq 'openai' -and $_.id -ceq 'gpt-6.1-sol'
    })
    if ($available.Count -ne 1 -or $available[0].enabled -ne $true -or $available[0].status -ne 'active') {
        throw 'STOP: openai/gpt-6.1-sol is not an enabled active model in this private project.'
    }
    $variants = @($available[0].variants | ForEach-Object { $_.id })
    if ($variants -cnotcontains 'high') {
        throw 'STOP: the private host does not advertise the approved Sol high variant.'
    }

    # The actual run below uses --model only for the model the human approved.
    # This verifies that route before the first paid request, without changing
    # any account, provider credentials, project config, or global defaults.
    $Evidence.ApprovedRunModel = $ApprovedModel
    $Evidence.ModelCatalogVerified = $true
}

function Assert-IsolatedPythonContextReady {
    param(
        [Parameter(Mandatory)][string]$FixtureRoot,
        [Parameter(Mandatory)][string]$PythonExecutable,
        [Parameter(Mandatory)][hashtable]$Evidence
    )

    # The generated plugin executes this exact interpreter from the fixture,
    # not from the source checkout. An import failure must cost zero tokens.
    $pluginPath = Join-Path $FixtureRoot '.opencode/plugins/evospec/index.ts'
    $pluginText = Get-Content -LiteralPath $pluginPath -Raw -Encoding utf8
    $agentMatch = [regex]::Match($pluginText, 'const AGENT_COMMAND = (?<command>\[[^\r\n]+\])')
    if (-not $agentMatch.Success) {
        throw 'STOP: isolated fixture plugin has no embedded Python agent command.'
    }
    $agentCommand = @($agentMatch.Groups['command'].Value | ConvertFrom-Json)
    if ($agentCommand.Count -ne 3 -or
        -not [string]::Equals([string]$agentCommand[0], $PythonExecutable, [StringComparison]::OrdinalIgnoreCase) -or
        $agentCommand[1] -cne '-m' -or $agentCommand[2] -cne 'evospec.agent_protocol') {
        throw 'STOP: fixture plugin does not use the isolated Python runtime.'
    }
    $Evidence.PythonCommandVerified = $true

    Push-Location $FixtureRoot
    try {
        # Resolve the installed package from the interpreter's own site-packages.
        # A source checkout on sys.path would hide the original Windows failure.
        $importCheck = 'import pathlib, sys, evospec.agent_protocol as agent; assert pathlib.Path(agent.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()), "EvoSpec is not installed in the isolated runtime"'
        $importResult = @(& $PythonExecutable -c $importCheck 2>&1)
        if ($LASTEXITCODE -ne 0) {
            throw "STOP: isolated Python cannot import its installed EvoSpec module: $($importResult -join ' ')"
        }
        $Evidence.PythonImportVerified = $true

        # Invoke the same read-only module path OpenCode will use, with no LLM.
        $protocolOutput = @('{"action":"context"}' | & $PythonExecutable -m evospec.agent_protocol --root $FixtureRoot 2>&1)
        if ($LASTEXITCODE -ne 0) {
            throw "STOP: isolated Python context protocol failed: $($protocolOutput -join ' ')"
        }
        $contextResponse = ($protocolOutput -join "`n") | ConvertFrom-Json -ErrorAction Stop
        $accepted = @($contextResponse.context.work_queue.all |
            Where-Object { $_.accepted_slice.feature_title -ceq 'Fixture temporal R2-D2' })
        if ($null -ne $contextResponse.error -or $accepted.Count -ne 1) {
            throw 'STOP: isolated Python did not return the synthetic EvoSpec context.'
        }
        $Evidence.PythonContextVerified = $true
    }
    finally { Pop-Location }
}

function New-VerifiedFixtureSession {
    param(
        [Parameter(Mandatory)][string]$FixtureRoot,
        [Parameter(Mandatory)][string]$ServerUrl,
        [Parameter(Mandatory)][string]$Authorization,
        [Parameter(Mandatory)][hashtable]$Evidence
    )

    # Give the private host an explicit session location. The CLI process
    # directory alone is not reliable when Windows inherits a stale PWD.
    $headers = @{ Authorization = $Authorization }
    $body = @{ location = @{ directory = $FixtureRoot }; title = 'EvoSpec R2-D2 synthetic qualification' } |
        ConvertTo-Json -Depth 4 -Compress
    try {
        $created = Invoke-RestMethod -Method Post -Uri "$ServerUrl/api/session" `
            -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 10
        $newSession = if ($null -ne $created.data) { $created.data } else { $created }
        $sessionId = [string]$newSession.id
        if ([string]::IsNullOrWhiteSpace($sessionId) -or $sessionId -notmatch '^ses_[A-Za-z0-9]+$') {
            throw 'private host returned no valid session identifier'
        }
        $read = Invoke-RestMethod -Method Get -Uri "$ServerUrl/api/session/$sessionId" `
            -Headers $headers -TimeoutSec 10
        $session = if ($null -ne $read.data) { $read.data } else { $read }
    }
    catch {
        throw "STOP: cannot create and inspect an explicit fixture session: $($_.Exception.Message)"
    }

    $reported = [string]$session.location.directory
    $expected = [System.IO.Path]::GetFullPath($FixtureRoot).TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($reported)) {
        throw 'STOP: the explicitly created session did not report a directory.'
    }
    $actual = [System.IO.Path]::GetFullPath($reported).TrimEnd('\', '/')
    if (-not [string]::Equals($expected, $actual, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "STOP: session location differs from the synthetic fixture: $reported"
    }
    $Evidence.SessionID = $sessionId
    $Evidence.SessionLocation = $reported
    $Evidence.SessionVerified = $true
    return $sessionId
}

# The replay path intentionally precedes Git, Python, OpenCode, and all host
# preparation. Existing traces are the only input to this offline gate.
if (-not [string]::IsNullOrWhiteSpace($ReplayEvidenceRoot)) {
    $root = (Resolve-Path -LiteralPath $ReplayEvidenceRoot -ErrorAction Stop).Path
    foreach ($case in @(
        @{ Name = 'parked-natural'; Scenario = 'parked' },
        @{ Name = 'parked-context'; Scenario = 'parked' },
        @{ Name = 'parked-status'; Scenario = 'parked' },
        @{ Name = 'ambiguous-natural'; Scenario = 'ambiguous' }
    )) {
        Assert-RecordedFixtureEvidence -EvidencePath (Join-Path $root "$($case.Name).ndjson") `
            -PreflightPath (Join-Path $root "preflight-$($case.Name).json") `
            -Scenario $case.Scenario -Name $case.Name
    }
    Write-Output 'R2D2_EVIDENCE_REPLAY: PASS (4/4; existing sessions only)'
    return
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
    if ($LASTEXITCODE -ne 0 -or $runHelp -notmatch '--server' -or $runHelp -notmatch '--session') {
        throw 'STOP: this OpenCode run command does not advertise --server and --session.'
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

    # Reproduce the documented installed-package path without changing global
    # Python, provider settings, or the checkout. The runtime lives beside the
    # disposable evidence; it is never created inside a checked fixture.
    $runtimeRoot = Join-Path $parent 'python-runtime'
    $runtimePython = Join-Path $runtimeRoot 'Scripts/python.exe'
    & python -m venv $runtimeRoot
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $runtimePython)) {
        throw 'STOP: cannot prepare an isolated Python runtime; no model calls were made.'
    }
    $installation = @(& $runtimePython -m pip install --no-deps --no-input --no-cache-dir `
        --disable-pip-version-check $sourceRoot 2>&1)
    $installation | ForEach-Object { $_.ToString() } |
        Set-Content -LiteralPath (Join-Path $evidenceRoot 'python-install.log') -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        throw 'STOP: isolated EvoSpec package installation failed; inspect python-install.log before any model call.'
    }
    foreach ($preparedFixture in $prepared.fixtures) {
        # Refresh the test-owned plugin to embed the installed interpreter.
        # Core state, synthetic agreements, and unrelated resources are untouched.
        $refreshOutput = @(& $runtimePython -m evospec.cli --project $preparedFixture.root integrate --refresh 2>&1)
        if ($LASTEXITCODE -ne 0) {
            throw "STOP: isolated EvoSpec plugin refresh failed for $($preparedFixture.scenario): $($refreshOutput -join ' ')"
        }
    }

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
            ApprovedRunModel = $ApprovedModel
            ModelCatalogVerified = $false
            PythonCommandVerified = $false
            PythonImportVerified = $false
            PythonContextVerified = $false
            SessionID = $null
            SessionLocation = $null
            SessionVerified = $false
            UnselectedHostDefault = 'NOT_READ'
            ModelCallsIssuedByRunnerBeforePreflight = 0
        }
        $serverProcess = $null
        try {
            Assert-IsolatedPythonContextReady -FixtureRoot $fixtureRoot -PythonExecutable $runtimePython -Evidence $preflight
            $port = Get-FreeLoopbackPort
            $serverUrl = "http://127.0.0.1:$port"
            # A fresh secret authenticates only this ephemeral loopback server.
            # It never enters Git, a persistent config, or the process command line.
            $privatePassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
            $basicCredential = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("opencode:$privatePassword"))
            # This private server remains alive for the exact session whose
            # plugin and location we inspect. No shared service is reused.
            # An encoded command preserves Windows paths and non-ASCII text;
            # retaining its process handle permits exact process-tree cleanup.
            $escapedRoot = $fixtureRoot.Replace("'", "''")
            $serveCommand = "Set-Location -LiteralPath '$escapedRoot'; & opencode serve --hostname 127.0.0.1 --port $port"
            $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($serveCommand))
            $serverProcess = Start-Process -FilePath (Get-Command pwsh -CommandType Application).Source `
                -ArgumentList @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encodedCommand) `
                -Environment @{ OPENCODE_PASSWORD = $privatePassword; OPENCODE_DISABLE_FREE_MODELS = '1' } `
                -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $evidenceRoot "server-$($job.Name).stdout.log") `
                -RedirectStandardError (Join-Path $evidenceRoot "server-$($job.Name).stderr.log")
            Assert-PrivateHostReady -FixtureRoot $fixtureRoot -ServerUrl $serverUrl -ServerProcess $serverProcess -Authorization "Basic $basicCredential" -Evidence $preflight
            Assert-ApprovedModelReady -FixtureRoot $fixtureRoot -ServerUrl $serverUrl -Authorization "Basic $basicCredential" -ApprovedModel $ApprovedModel -Evidence $preflight
            $fixtureSessionId = New-VerifiedFixtureSession -FixtureRoot $fixtureRoot -ServerUrl $serverUrl -Authorization "Basic $basicCredential" -Evidence $preflight

            Push-Location $fixtureRoot
            try {
                if ((Get-Location).ProviderPath -ne $fixtureRoot) {
                    throw "STOP: shell did not enter the isolated fixture $fixtureRoot."
                }
                # This is the human-approved model for this isolated smoke.
                # --auto handles only non-denied host permissions.
                $previousPassword = [Environment]::GetEnvironmentVariable('OPENCODE_PASSWORD', 'Process')
                $previousPwd = [Environment]::GetEnvironmentVariable('PWD', 'Process')
                try {
                    $env:OPENCODE_PASSWORD = $privatePassword
                    # OpenCode V2 may prefer the inherited PWD over process cwd.
                    # Set it only for this call, then restore the exact prior value.
                    $env:PWD = $fixtureRoot
                    $outputLines = @(& opencode run --server $serverUrl --session $fixtureSessionId --model $ApprovedModel --auto --agent build --format json $job.Prompt 2>&1)
                    $exitCode = $LASTEXITCODE
                }
                finally {
                    # Restore precisely the caller's original process environment.
                    if ($null -eq $previousPassword) {
                        Remove-Item Env:OPENCODE_PASSWORD -ErrorAction SilentlyContinue
                    } else {
                        $env:OPENCODE_PASSWORD = $previousPassword
                    }
                    if ($null -eq $previousPwd) {
                        Remove-Item Env:PWD -ErrorAction SilentlyContinue
                    } else {
                        $env:PWD = $previousPwd
                    }
                }
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
                if (-not [string]::IsNullOrEmpty($privatePassword)) {
                    # OpenCode may echo its server password on stdout. Preserve
                    # diagnostics without leaving that temporary secret in logs.
                    foreach ($logName in @("server-$($job.Name).stdout.log", "server-$($job.Name).stderr.log")) {
                        $logPath = Join-Path $evidenceRoot $logName
                        if (-not (Test-Path -LiteralPath $logPath)) { continue }
                        $rawLog = Get-Content -LiteralPath $logPath -Raw -Encoding utf8
                        if ([string]::IsNullOrEmpty($rawLog)) { continue }
                        $safeLog = $rawLog.Replace($privatePassword, '[REDACTED]').Replace($basicCredential, '[REDACTED]')
                        if ($safeLog -cne $rawLog) {
                            Set-Content -LiteralPath $logPath -Value $safeLog -Encoding utf8 -NoNewline
                        }
                    }
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
        $observedSessionIds = @(
            Get-Content -LiteralPath $outputPath -Encoding utf8 |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop } |
                Where-Object { $_.type -eq 'step_start' } |
                ForEach-Object { $_.sessionID } |
                Sort-Object -Unique
        )
        if ($observedSessionIds.Count -ne 1 -or $observedSessionIds[0] -cne $fixtureSessionId) {
            throw "STOP: native events do not belong to the preflight-verified fixture session. Evidence: $outputPath"
        }
        $contextCalls = @(Get-CanonicalContextCalls -EvidencePath $outputPath)
        $matchedContext = @($contextCalls | Where-Object {
            Test-CanonicalFixtureContext -Output ([string]$_.part.state.output) -Scenario $job.Scenario
        })
        if ($matchedContext.Count -eq 0) {
            if ($contextCalls.Count -gt 0) {
                throw "STOP: $($job.Name) invoked evospec_context, but it did not return the synthetic fixture context. No more model sessions. Evidence: $outputPath"
            }
            throw "STOP: $($job.Name) did not call evospec_context for its fixture. No more model sessions. Evidence: $outputPath"
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
        Model = $ApprovedModel
        ModelSelection = 'EXPLICIT_HUMAN_APPROVED_TEST_ONLY'
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
