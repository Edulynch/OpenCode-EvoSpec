# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.building.api import COLLECT, EXE, MERGE, PYZ
from PyInstaller.building.build_main import Analysis
from pathlib import Path


ROOT = Path(SPECPATH).parent
package_resources = [
    (str(ROOT / "evospec/resources/opencode"), "evospec/resources/opencode"),
    (str(ROOT / "evospec/resources/dashboard"), "evospec/resources/dashboard"),
    (str(ROOT / "pyproject.toml"), "."),
]

evospec_analysis = Analysis(
    [str(ROOT / "packaging/entrypoints/evospec_cli.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=package_resources,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
evospec_agent_analysis = Analysis(
    [str(ROOT / "packaging/entrypoints/evospec_agent.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
evospec_agent_protocol_analysis = Analysis(
    [str(ROOT / "packaging/entrypoints/evospec_agent_protocol.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

MERGE(
    (evospec_analysis, "evospec", "evospec"),
    (evospec_agent_analysis, "evospec-agent", "evospec-agent"),
    (
        evospec_agent_protocol_analysis,
        "evospec-agent-protocol",
        "evospec-agent-protocol",
    ),
)

evospec_pyz = PYZ(evospec_analysis.pure)
evospec_agent_pyz = PYZ(evospec_agent_analysis.pure)
evospec_agent_protocol_pyz = PYZ(evospec_agent_protocol_analysis.pure)

evospec_exe = EXE(
    evospec_pyz,
    evospec_analysis.scripts,
    [],
    exclude_binaries=True,
    name="evospec",
    console=True,
)
evospec_agent_exe = EXE(
    evospec_agent_pyz,
    evospec_agent_analysis.scripts,
    [],
    exclude_binaries=True,
    name="evospec-agent",
    console=True,
)
evospec_agent_protocol_exe = EXE(
    evospec_agent_protocol_pyz,
    evospec_agent_protocol_analysis.scripts,
    [],
    exclude_binaries=True,
    name="evospec-agent-protocol",
    console=True,
)

bundle = COLLECT(
    evospec_exe,
    evospec_analysis.binaries,
    evospec_analysis.zipfiles,
    evospec_analysis.datas,
    evospec_agent_exe,
    evospec_agent_analysis.binaries,
    evospec_agent_analysis.zipfiles,
    evospec_agent_analysis.datas,
    evospec_agent_protocol_exe,
    evospec_agent_protocol_analysis.binaries,
    evospec_agent_protocol_analysis.zipfiles,
    evospec_agent_protocol_analysis.datas,
    name="evospec",
)
