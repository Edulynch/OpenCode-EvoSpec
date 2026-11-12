#!/bin/sh
set -eu

tag=latest
asset_directory=
manifest_path=
install_root="${XDG_DATA_HOME:-$HOME/.local/share}/evospec"
profile_file="${PROFILE:-$HOME/.profile}"
uninstall=false

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag) tag=$2; shift 2 ;;
        --asset-directory) asset_directory=$2; shift 2 ;;
        --manifest-path) manifest_path=$2; shift 2 ;;
        --install-root) install_root=$2; shift 2 ;;
        --profile-file) profile_file=$2; shift 2 ;;
        --uninstall) uninstall=true; shift ;;
        *) printf '%s\n' "unknown argument: $1" >&2; exit 2 ;;
    esac
done

current="$install_root/current"
marker="$install_root/.evospec-install"
path_line="export PATH=\"$current:\$PATH\" # evospec-installer"
failure() {
    if [ "${EVOSPEC_INSTALL_TEST_FAIL_AT:-}" = "$1" ]; then
        printf '%s\n' "injected installer failure: $1" >&2
        exit 1
    fi
}
prepare_profile() {
    mkdir -p "$(dirname "$profile_file")"
    [ -f "$profile_file" ] && cp "$profile_file" "$profile_candidate" || : > "$profile_candidate"
    grep -Fvx "$path_line" "$profile_candidate" > "$profile_candidate.filtered" || true
    mv "$profile_candidate.filtered" "$profile_candidate"
    if [ "$1" = present ]; then printf '%s\n' "$path_line" >> "$profile_candidate"; fi
}
validate_release_segment() {
    case "$1" in
        ""|[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*)
            printf '%s\n' 'invalid release tag' >&2
            exit 1
            ;;
    esac
}

download_failure() {
    rm -rf "$asset_directory"
    if [ "$install_root_existed" = false ]; then rmdir "$install_root" 2>/dev/null || true; fi
    printf '%s\n' "$1" >&2
    exit 1
}

download_public_asset() {
    if ! curl --disable --fail --location --silent --show-error --output "$2" "$1"; then
        download_failure 'Public GitHub release download failed; the repository and release must be public, or authenticate with gh.'
    fi
}

if [ "$uninstall" = true ]; then
    if [ -f "$marker" ]; then
        rm -rf "$current" "$install_root/versions" "$marker"
        transaction="$install_root/.uninstall-$$"
        profile_candidate="$transaction/profile"
        mkdir -p "$transaction"
        prepare_profile absent
        mv "$profile_candidate" "$profile_file"
        rm -rf "$transaction"
    fi
    exit 0
fi

platform="$(uname -s)-$(uname -m)"
case "$platform" in
    Linux-x86_64) platform=linux-x86_64 ;;
    Darwin-arm64) platform=macos-arm64 ;;
    Darwin-x86_64) platform=macos-x86_64 ;;
    *) printf '%s\n' "unsupported platform: $platform" >&2; exit 1 ;;
esac
case "$platform" in
    linux-x86_64) ;;
    *) printf '%s\n' "unsupported platform: $platform" >&2; exit 1 ;;
esac
authenticated=false
if [ -z "$asset_directory" ] && gh auth status --hostname github.com >/dev/null 2>&1; then authenticated=true; fi
if [ -z "$asset_directory" ] && [ "$authenticated" = false ] && [ "$tag" != latest ]; then validate_release_segment "$tag"; fi
install_root_existed=false
if [ -e "$install_root" ] || [ -L "$install_root" ]; then install_root_existed=true; fi
mkdir -p "$install_root"
if { [ -e "$current" ] || [ -L "$current" ]; } && [ ! -f "$marker" ]; then
    printf '%s\n' 'refusing to replace an installation not owned by EvoSpec' >&2
    exit 1
fi
if [ -n "$asset_directory" ]; then
    [ -n "$manifest_path" ] || { printf '%s\n' "--manifest-path is required with --asset-directory" >&2; exit 2; }
else
    asset_directory="$install_root/.download-$$"
    mkdir -p "$asset_directory"
    download=remote
    manifest_path="$asset_directory/SHA256SUMS"
    if [ "$authenticated" = true ]; then
        if [ "$tag" = latest ]; then
            if ! gh release download --repo Edulynch/OpenCode-EvoSpec --pattern SHA256SUMS --dir "$asset_directory"; then download_failure 'GitHub release download failed'; fi
        else
            if ! gh release download "v$tag" --repo Edulynch/OpenCode-EvoSpec --pattern SHA256SUMS --dir "$asset_directory"; then download_failure 'GitHub release download failed'; fi
        fi
    else
        if [ "$tag" = latest ]; then
            manifest_url='https://github.com/Edulynch/OpenCode-EvoSpec/releases/latest/download/SHA256SUMS'
        else
            manifest_url="https://github.com/Edulynch/OpenCode-EvoSpec/releases/download/v${tag}/SHA256SUMS"
        fi
        download_public_asset "$manifest_url" "$manifest_path"
    fi
fi
record="$(grep -E "^[0-9a-f]{64}  evospec-[A-Za-z0-9][A-Za-z0-9._-]*-$platform\\.zip$" "$manifest_path")"
[ "$(printf '%s\n' "$record" | wc -l | tr -d ' ')" = 1 ] || { printf '%s\n' "invalid SHA256SUMS allowlist" >&2; exit 1; }
checksum="${record%%  *}"
archive="${record#*  }"
[ "$tag" = latest ] || [ "$archive" = "evospec-$tag-$platform.zip" ] || { printf '%s\n' "manifest tag mismatch" >&2; exit 1; }
if [ -n "${download:-}" ]; then
    version="${archive#evospec-}"; version="${version%-$platform.zip}"
    if [ "$authenticated" = true ]; then
        if ! gh release download "v$version" --repo Edulynch/OpenCode-EvoSpec --pattern "$archive" --dir "$asset_directory"; then download_failure 'GitHub release download failed'; fi
    else
        validate_release_segment "$version"
        download_public_asset "https://github.com/Edulynch/OpenCode-EvoSpec/releases/download/v${version}/${archive}" "$asset_directory/$archive"
    fi
fi
if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$checksum" "$archive" | (cd "$asset_directory" && sha256sum -c -)
else
    printf '%s  %s\n' "$checksum" "$archive" | (cd "$asset_directory" && shasum -a 256 -c -)
fi
entries="$(unzip -Z1 "$asset_directory/$archive")"
required="evospec/evospec evospec/evospec-agent evospec/evospec-agent-protocol"
for entry in $entries; do
    case "$entry" in
        *\\*|/*|?:*|*"//"*|*/./*|*/../*|.|..|*/.|*/..) printf '%s\n' "unsafe archive" >&2; exit 1 ;;
        evospec/evospec|evospec/evospec-agent|evospec/evospec-agent-protocol|evospec/_internal/*|evospec/|evospec/_internal/) ;;
        *) printf '%s\n' "unsafe archive" >&2; exit 1 ;;
    esac
done
for entry in $required; do printf '%s\n' "$entries" | grep -Fx "$entry" >/dev/null || { printf '%s\n' "archive is missing a public executable" >&2; exit 1; }; done
[ -z "$(printf '%s\n' "$entries" | sort | uniq -d)" ] || { printf '%s\n' "unsafe archive" >&2; exit 1; }
unzip -tqq "$asset_directory/$archive"
version="${archive#evospec-}"; version="${version%-$platform.zip}"
stage="$install_root/.stage-$version-$$"
transaction="$install_root/.transaction-$version-$$"
target="$install_root/versions/$version"
candidate="$install_root/versions/.candidate-$version-$$"
profile_candidate="$transaction/profile-candidate"
profile_snapshot="$transaction/profile-snapshot"
cleanup() {
    rm -rf "$stage" "$transaction" "$candidate"
    if [ -n "${download:-}" ]; then rm -rf "$asset_directory"; fi
}
rollback() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$rollback_ready" = true ]; then
        [ "$current_created" = true ] && rm -rf "$current"
        [ "$target_created" = true ] && rm -rf "$target"
        [ ! -e "$transaction/target" ] || mv "$transaction/target" "$target"
        [ ! -e "$transaction/current" ] && [ ! -L "$transaction/current" ] || mv "$transaction/current" "$current"
        if [ "$marker_existed" = true ]; then cp "$transaction/marker" "$marker"; elif [ "$marker_created" = true ]; then rm -f "$marker"; fi
        if [ "$profile_existed" = true ]; then cp "$profile_snapshot" "$profile_file"; elif [ "$profile_created" = true ]; then rm -f "$profile_file"; fi
    fi
    cleanup
    exit "$status"
}
trap rollback EXIT HUP INT TERM
rollback_ready=false
current_created=false
target_created=false
marker_created=false
profile_created=false
marker_existed=false
profile_existed=false
rm -rf "$stage"; mkdir -p "$stage"
unzip -q "$asset_directory/$archive" -d "$stage"
chmod +x "$stage/evospec/evospec" "$stage/evospec/evospec-agent" "$stage/evospec/evospec-agent-protocol"
failure pre-swap
"$stage/evospec/evospec" --version >/dev/null
mkdir -p "$transaction"
if [ -f "$marker" ]; then cp "$marker" "$transaction/marker"; marker_existed=true; fi
if [ -f "$profile_file" ]; then cp "$profile_file" "$profile_snapshot"; profile_existed=true; fi
prepare_profile present
rollback_ready=true
[ ! -e "$current" ] && [ ! -L "$current" ] || mv "$current" "$transaction/current"
[ ! -e "$target" ] || mv "$target" "$transaction/target"
failure promotion
mkdir -p "$(dirname "$candidate")"
mv "$stage/evospec" "$candidate"
mv "$candidate" "$target"
target_created=true
ln -s "$target" "$current"
current_created=true
failure final-smoke
"$current/evospec" --version >/dev/null
failure ownership
printf '%s' 'EvoSpec installer owns this directory' > "$marker"
marker_created=true
mv "$profile_candidate" "$profile_file"
profile_created=true
trap - EXIT HUP INT TERM
cleanup
