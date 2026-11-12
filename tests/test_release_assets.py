from __future__ import annotations

import hashlib
import unittest
import zipfile
from pathlib import Path

from tests._tempdir import TemporaryDirectory


class ReleaseAssetTests(unittest.TestCase):
    def test_assembly_writes_exact_sorted_checksums_and_deterministic_zip_names(self) -> None:
        # Given: each currently distributed platform has a native bundle fixture
        from scripts.assemble_release_assets import assemble_release_assets

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundles = {
                platform: self._bundle(root / platform, platform)
                for platform in (
                    "windows-x86_64",
                    "linux-x86_64",
                )
            }
            output = root / "release"

            # When: the release assets are assembled twice from the same inputs
            first = assemble_release_assets("1.2.3", bundles, output)
            first_bytes = {path.name: path.read_bytes() for path in first}
            second = assemble_release_assets("1.2.3", bundles, output)

            # Then: names, checksums, and bytes are stable and allowlisted
            expected = [
                "evospec-1.2.3-linux-x86_64.zip",
                "evospec-1.2.3-windows-x86_64.zip",
            ]
            self.assertEqual([path.name for path in first], expected)
            self.assertEqual(first_bytes, {path.name: path.read_bytes() for path in second})
            checksums = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                checksums,
                [
                    f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}"
                    for name in expected
                ],
            )
            with zipfile.ZipFile(output / expected[0]) as archive:
                self.assertEqual(archive.namelist(), ["evospec/README.txt"])

    def test_assembly_rejects_missing_duplicate_or_unsafe_bundle_content(self) -> None:
        # Given: a valid current-distribution platform map with a source symlink
        from scripts.assemble_release_assets import assemble_release_assets

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundles = {
                platform: self._bundle(root / platform, platform)
                for platform in (
                    "windows-x86_64",
                    "linux-x86_64",
                )
            }
            (bundles["linux-x86_64"] / "link").symlink_to("README.txt")

            # When/Then: malformed platform sets and unsafe sources are rejected
            with self.assertRaises(ValueError):
                _ = assemble_release_assets("1.2.3", {"linux-x86_64": bundles["linux-x86_64"]}, root / "release")
            with self.assertRaises(ValueError):
                _ = assemble_release_assets("1.2.3", bundles, root / "release")
            with self.assertRaises(ValueError):
                _ = assemble_release_assets(
                    "1.2.3",
                    {
                        "windows-x86_64": bundles["windows-x86_64"],
                        "linux-x86_64": bundles["linux-x86_64"],
                        "macos-arm64": root / "macos",
                    },
                    root / "release",
                )

    def test_platform_asset_uses_platform_public_names_and_rejects_unsafe_members(self) -> None:
        # Given: a Windows native bundle with its three public executables
        from scripts.assemble_release_assets import (
            assemble_platform_asset,
            validate_platform_asset,
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root / "windows", "windows-x86_64")
            for name in ("evospec.exe", "evospec-agent.exe", "evospec-agent-protocol.exe"):
                (bundle / name).touch()
            output = root / "native"

            # When: a single platform asset is assembled and then validated
            asset = assemble_platform_asset("1.2.3", "windows-x86_64", bundle, output)
            validate_platform_asset("1.2.3", "windows-x86_64", output)

            # Then: it uses the Windows public executable name and safe archive layout
            self.assertEqual(asset.name, "evospec-1.2.3-windows-x86_64.zip")
            with zipfile.ZipFile(asset) as archive:
                self.assertIn("evospec/evospec.exe", archive.namelist())
            with zipfile.ZipFile(asset, "a") as archive:
                archive.writestr("evospec/evospec.exe", "duplicate")
            with self.assertRaises(ValueError):
                validate_platform_asset("1.2.3", "windows-x86_64", output)
            asset = assemble_platform_asset("1.2.3", "windows-x86_64", bundle, output)
            with zipfile.ZipFile(asset, "a") as archive:
                archive.writestr("evospec/../unsafe", "unsafe")
            with self.assertRaises(ValueError):
                validate_platform_asset("1.2.3", "windows-x86_64", output)

    def _bundle(self, root: Path, platform: str) -> Path:
        _ = root.mkdir(parents=True)
        (root / "README.txt").write_text(platform, encoding="utf-8")
        return root
