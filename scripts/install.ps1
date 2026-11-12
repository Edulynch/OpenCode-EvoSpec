[CmdletBinding()]
param(
    [string]$Tag = "latest",
    [string]$AssetDirectory,
    [string]$ManifestPath,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "EvoSpec"),
    [string]$PathFile,
    [switch]$Uninstall,
    [switch]$Bootstrap
)

$ErrorActionPreference = "Stop"
$Platform = "windows-x86_64"
$Repository = "Edulynch/OpenCode-EvoSpec"

Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class EvoSpecPathIdentity {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string name, uint desiredAccess, FileShare share, IntPtr securityAttributes,
        uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle handle, StringBuilder path, uint length, uint flags);

    public static string CanonicalPath(string path) {
        using (SafeFileHandle handle = CreateFile(path, 0, FileShare.ReadWrite | FileShare.Delete, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if (handle.IsInvalid) return string.Empty;
            StringBuilder buffer = new StringBuilder(32768);
            return GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0) == 0 ? string.Empty : buffer.ToString();
        }
    }
}
'@

function Get-PathEntries([string]$Value) {
    return @($Value -split [regex]::Escape([IO.Path]::PathSeparator) | Where-Object { $_ })
}

function Get-PathIdentity([string]$Value) {
    try {
        $full = [IO.Path]::GetFullPath($Value)
        $canonical = [EvoSpecPathIdentity]::CanonicalPath($full)
        if ($canonical) { $full = $canonical }
        if ($full.StartsWith("\\?\UNC\", [StringComparison]::OrdinalIgnoreCase)) {
            $full = "\\" + $full.Substring(8)
        } elseif ($full.StartsWith("\\?\", [StringComparison]::OrdinalIgnoreCase)) {
            $full = $full.Substring(4)
        }
        return [IO.Path]::TrimEndingDirectorySeparator($full)
    } catch {
        return $null
    }
}

function Test-OwnedPath([string]$Entry, [string]$CurrentIdentity) {
    $entryIdentity = Get-PathIdentity $Entry
    return $entryIdentity -and [StringComparer]::OrdinalIgnoreCase.Equals($entryIdentity, $CurrentIdentity)
}

function Set-OwnedPath([string]$Current, [bool]$Present) {
    $currentIdentity = Get-PathIdentity $Current
    $processEntries = @($env:PATH -split [regex]::Escape([IO.Path]::PathSeparator) | Where-Object { -not (Test-OwnedPath $_ $currentIdentity) })
    if ($Present) { $processEntries = @($Current) + $processEntries }
    if ($PathFile) {
        $value = if (Test-Path -LiteralPath $PathFile) { Get-Content -LiteralPath $PathFile -Raw } else { "" }
        $entries = @((Get-PathEntries $value) | Where-Object { -not (Test-OwnedPath $_ $currentIdentity) })
        if ($Present) { $entries += @($Current) }
        Set-Content -LiteralPath $PathFile -Value ($entries -join [IO.Path]::PathSeparator) -NoNewline
    } else {
        $value = [Environment]::GetEnvironmentVariable("Path", "User")
        $entries = @((Get-PathEntries $value) | Where-Object { -not (Test-OwnedPath $_ $currentIdentity) })
        if ($Present) { $entries += @($Current) }
        [Environment]::SetEnvironmentVariable("Path", ($entries -join [IO.Path]::PathSeparator), "User")
    }
    $env:PATH = $processEntries -join [IO.Path]::PathSeparator
}

function Restore-PathState([byte[]]$PathBytes, [bool]$PathExisted, [string]$UserPath) {
    if ($PathFile) {
        if ($PathExisted) { [IO.File]::WriteAllBytes($PathFile, $PathBytes) } elseif (Test-Path -LiteralPath $PathFile) { Remove-Item -LiteralPath $PathFile -Force }
        return
    }
    [Environment]::SetEnvironmentVariable("Path", $UserPath, "User")
}

function Restore-MarkerState([byte[]]$MarkerBytes, [bool]$MarkerExisted, [string]$Marker) {
    if ($MarkerExisted) { [IO.File]::WriteAllBytes($Marker, $MarkerBytes) } elseif (Test-Path -LiteralPath $Marker) { Remove-Item -LiteralPath $Marker -Force }
}

function Test-OwnedInstallMarker([string]$Marker) {
    try {
        if (-not (Test-Path -LiteralPath $Marker -PathType Leaf)) { return $false }
        $markerFile = Get-Item -LiteralPath $Marker -Force -ErrorAction Stop
        if ($markerFile.PSIsContainer -or (($markerFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { return $false }
        return ([IO.File]::ReadAllText($Marker) -ceq "EvoSpec installer owns this directory")
    } catch {
        return $false
    }
}
function Test-GitHubAuthentication {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { return $false }
    & gh auth status --hostname github.com 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-EncodedReleaseSegment([string]$Value) {
    if ($Value -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { throw "invalid release path segment" }
    return [Uri]::EscapeDataString($Value)
}

function Invoke-PublicReleaseDownload([string]$Url, [string]$Destination) {
    $uri = [Uri]$Url
    if ($uri.Scheme -ne "https" -or $uri.Host -ne "github.com") { throw "refusing a non-GitHub public release URL" }
    try {
        Invoke-WebRequest -Uri $uri -OutFile $Destination -ErrorAction Stop | Out-Null
    } catch {
        throw "Public GitHub release download failed; the repository and release must be public, or authenticate with gh."
    }
}

function Get-ReleaseTag([string]$RequestedTag) {
    if ($RequestedTag -ne "latest") { return "v$RequestedTag" }
    $releaseTag = [string](& gh release view --repo $Repository --json tagName --jq .tagName)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -or -not $releaseTag) { throw "GitHub release lookup failed" }
    return $releaseTag.Trim()
}

function Get-ReleaseFiles([string]$DownloadRoot, [string]$ReleaseTag, [string]$RequestedTag, [bool]$PublicDownload) {
    if ($AssetDirectory) {
        if (-not $ManifestPath) { throw "-ManifestPath is required with -AssetDirectory" }
        return [pscustomobject]@{ AssetRoot = [IO.Path]::GetFullPath($AssetDirectory); Manifest = [IO.Path]::GetFullPath($ManifestPath) }
    }
    New-Item -ItemType Directory -Path $DownloadRoot | Out-Null
    $manifest = Join-Path $DownloadRoot "SHA256SUMS"
    if ($PublicDownload) {
        $manifestUrl = if ($RequestedTag -eq "latest") {
            "https://github.com/$Repository/releases/latest/download/SHA256SUMS"
        } else {
            $tagSegment = Get-EncodedReleaseSegment "v$RequestedTag"
            "https://github.com/$Repository/releases/download/$tagSegment/SHA256SUMS"
        }
        Invoke-PublicReleaseDownload $manifestUrl $manifest
    } else {
        & gh release download $ReleaseTag --repo $Repository --pattern SHA256SUMS --output $manifest
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) { throw "GitHub release download failed" }
    }
    return [pscustomobject]@{ AssetRoot = $DownloadRoot; Manifest = $manifest; ReleaseTag = $ReleaseTag }
}

function Get-ArchiveName([string]$Manifest, [string]$RequestedTag) {
    $matches = @(Get-Content -LiteralPath $Manifest | Where-Object { $_ -match "^[0-9a-f]{64}  evospec-[A-Za-z0-9][A-Za-z0-9._-]*-$Platform\.zip$" })
    if ($matches.Count -ne 1) { throw "SHA256SUMS must contain exactly one $Platform archive" }
    $name = $matches[0].Substring(66)
    if ($RequestedTag -ne "latest" -and $name -ne "evospec-$RequestedTag-$Platform.zip") { throw "SHA256SUMS does not match requested tag" }
    return $name
}

function Test-Archive([string]$Archive, [string]$Manifest, [string]$Name) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLower()
    if (@(Get-Content -LiteralPath $Manifest | Where-Object { $_ -eq "$hash  $Name" }).Count -ne 1) { throw "archive checksum is absent or invalid" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $required = @("evospec/evospec.exe", "evospec/evospec-agent.exe", "evospec/evospec-agent-protocol.exe")
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            $member = $entry.FullName
            if (-not $member -or $member -match "\\" -or $member -match "//" -or $member -match "^/" -or $member -match "^[A-Za-z]:" -or $member -match "(^|/)(\.|\.\.)(/|$)" -or -not $seen.Add($member)) { throw "archive has unsafe or duplicate paths" }
            if (-not $entry.Name) {
                if ($member -notin @("evospec/", "evospec/_internal/")) { throw "archive has unexpected directory" }
                continue
            }
            if ($member -in $required) { continue }
            $isInternalPath = $member -match "^evospec/_internal/[A-Za-z0-9][A-Za-z0-9._/-]*$"
            $isDirectPrivateExtension = $member -match "^evospec/_internal/_[A-Za-z0-9][A-Za-z0-9._-]*\.pyd$"
            if (-not ($isInternalPath -or $isDirectPrivateExtension)) { throw "archive has unexpected public path" }
        }
        foreach ($member in $required) { if (-not $seen.Contains($member)) { throw "archive is missing a public executable" } }
    } finally { $zip.Dispose() }
}

function Expand-CheckedArchive([string]$Archive, [string]$Destination) {
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
}

function Invoke-Smoke([string]$Bundle) {
    & (Join-Path $Bundle "evospec.exe") --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "native smoke check failed" }
}

function Test-FailurePoint([string]$Name) {
    if (@($env:EVOSPEC_INSTALL_TEST_FAIL_AT -split "," | ForEach-Object { $_.Trim() }) -contains $Name) { throw "injected installer failure: $Name" }
}

function Invoke-RollbackAction([string]$Name, [scriptblock]$Action, [Collections.Generic.List[string]]$Failures) {
    try {
        Test-FailurePoint "rollback-$Name"
        & $Action
    } catch {
        $Failures.Add("${Name}: $($_.Exception.Message)")
    }
}

$root = [IO.Path]::GetFullPath($InstallRoot)
$current = Join-Path $root "current"
$marker = Join-Path $root ".evospec-install"
if ($Uninstall) {
    if (Test-OwnedInstallMarker $marker) {
        Set-OwnedPath $current $false
        Remove-Item -LiteralPath $current -Recurse -Force
        Remove-Item -LiteralPath (Join-Path $root "versions") -Recurse -Force
        Remove-Item -LiteralPath $marker -Force
    }
    exit 0
}

if (((Test-Path -LiteralPath $current) -or (Test-Path -LiteralPath $marker)) -and -not (Test-OwnedInstallMarker $marker)) { throw "refusing to replace an installation not owned by EvoSpec" }
$downloadRoot = if ($AssetDirectory) { $null } else { Join-Path ([IO.Path]::GetTempPath()) ("evospec-download-" + [IO.Path]::GetRandomFileName()) }
$staging = $null
$transactionDisposition = "retain"
$installFailure = $null
try {
    $publicDownload = $false
    $releaseTag = $null
    if (-not $AssetDirectory) {
        $publicDownload = -not (Test-GitHubAuthentication)
        if ($publicDownload -and $Tag -ne "latest") { $null = Get-EncodedReleaseSegment "v$Tag" }
        if (-not $publicDownload) { $releaseTag = Get-ReleaseTag $Tag }
    }
    $files = Get-ReleaseFiles $downloadRoot $releaseTag $Tag $publicDownload
    $name = Get-ArchiveName $files.Manifest $Tag
    $version = $name.Substring(8, $name.Length - 8 - "-$Platform.zip".Length)
    $archive = Join-Path $files.AssetRoot $name
    if (-not (Test-Path -LiteralPath $archive)) {
        if ($AssetDirectory) { throw "fixture archive is missing" }
        if ($publicDownload) {
            $versionSegment = Get-EncodedReleaseSegment "v$version"
            $assetSegment = Get-EncodedReleaseSegment $name
            $assetUrl = "https://github.com/$Repository/releases/download/$versionSegment/$assetSegment"
            Invoke-PublicReleaseDownload $assetUrl $archive
        } else {
            & gh release download $files.ReleaseTag --repo $Repository --pattern $name --output $archive
            $exitCode = $LASTEXITCODE
            if ($exitCode -ne 0) { throw "GitHub release download failed" }
        }
    }
    Test-Archive $archive $files.Manifest $name
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $staging = Join-Path $root ".stage-$version-$PID"
    $transaction = Join-Path $root ".transaction-$version-$PID"
    $target = Join-Path (Join-Path $root "versions") $version
    $backupCurrent = Join-Path $transaction "current"
    $backupTarget = Join-Path $transaction "target"
    New-Item -ItemType Directory -Path $staging | Out-Null
    Expand-CheckedArchive $archive $staging
    Test-FailurePoint "pre-swap"
    Invoke-Smoke (Join-Path $staging "evospec")
    New-Item -ItemType Directory -Path $transaction | Out-Null
    $transactionDisposition = "cleanup"
    $markerExisted = Test-Path -LiteralPath $marker
    $markerBytes = if ($markerExisted) { [IO.File]::ReadAllBytes($marker) } else { [byte[]]@() }
    $pathExisted = $PathFile -and (Test-Path -LiteralPath $PathFile)
    $pathBytes = if ($pathExisted) { [IO.File]::ReadAllBytes($PathFile) } else { [byte[]]@() }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $processPath = $env:PATH
    $currentCreated = $false
    $targetCreated = $false
    $markerRestoreArmed = $true
    $pathRestoreArmed = $true
    $transactionDisposition = "retain"
    try {
        if (Test-Path -LiteralPath $current) { Move-Item -LiteralPath $current -Destination $backupCurrent }
        if (Test-Path -LiteralPath $target) { Move-Item -LiteralPath $target -Destination $backupTarget }
        Test-FailurePoint "promotion"
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Move-Item -LiteralPath (Join-Path $staging "evospec") -Destination $target
        $targetCreated = $true
        New-Item -ItemType Junction -Path $current -Target $target | Out-Null
        $currentCreated = $true
        Test-FailurePoint "final-smoke"
        Invoke-Smoke $current
        Test-FailurePoint "ownership"
        Set-Content -LiteralPath $marker -Value "EvoSpec installer owns this directory" -NoNewline
        Test-FailurePoint "after-marker"
        Set-OwnedPath $current $true
        Test-FailurePoint "after-path"
        $transactionDisposition = "cleanup"
    } catch {
        $installFailure = $_
        $env:PATH = $processPath
        $rollbackFailures = [Collections.Generic.List[string]]::new()
        if ($currentCreated) { Invoke-RollbackAction "remove-current" { Remove-Item -LiteralPath $current -Recurse -Force } $rollbackFailures }
        if ($targetCreated) { Invoke-RollbackAction "remove-target" { Remove-Item -LiteralPath $target -Recurse -Force } $rollbackFailures }
        if (Test-Path -LiteralPath $backupTarget) { Invoke-RollbackAction "target" { Move-Item -LiteralPath $backupTarget -Destination $target } $rollbackFailures }
        if (Test-Path -LiteralPath $backupCurrent) { Invoke-RollbackAction "current" { Move-Item -LiteralPath $backupCurrent -Destination $current } $rollbackFailures }
        if ($markerRestoreArmed) { Invoke-RollbackAction "marker" { Restore-MarkerState $markerBytes $markerExisted $marker } $rollbackFailures }
        if ($pathRestoreArmed) { Invoke-RollbackAction "path" { Restore-PathState $pathBytes $pathExisted $userPath } $rollbackFailures }
        if ($rollbackFailures.Count -gt 0) {
            [Console]::Error.WriteLine("rollback failed; recovery data retained at $transaction")
        } else {
            $transactionDisposition = "cleanup"
        }
        throw $installFailure
    }
} catch {
    $installFailure = $_
    throw
} finally {
    $cleanupFailure = $null
    $cleanupPaths = @($staging)
    if ($transactionDisposition -eq "cleanup") { $cleanupPaths += $transaction }
    $cleanupPaths += $downloadRoot
    foreach ($cleanupPath in $cleanupPaths) {
        try {
            if ($cleanupPath -and (Test-Path -LiteralPath $cleanupPath)) { Remove-Item -LiteralPath $cleanupPath -Recurse -Force }
        } catch {
            if (-not $cleanupFailure) { $cleanupFailure = $_ }
            [Console]::Error.WriteLine("cleanup failed for ${cleanupPath}: $($_.Exception.Message)")
        }
    }
    if ($Bootstrap) {
        try {
            Remove-Item -LiteralPath $PSCommandPath -Force
        } catch {
            if (-not $cleanupFailure) { $cleanupFailure = $_ }
            [Console]::Error.WriteLine("cleanup failed for ${PSCommandPath}: $($_.Exception.Message)")
        }
    }
    if (-not $installFailure -and $cleanupFailure) { throw $cleanupFailure }
}
Write-Output "EvoSpec $version installed successfully."
