"""The shipped automation wrappers load on a PC other than the one that built the installer.

First tester, 13 November: every look at the screen failed with "cannot import name
'OLE_YSIZE_HIMETRIC'". Each wrapper ended with the date of a Windows file on the build PC, no
other PC matched it, and the wrappers were made again by several threads at once.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _build():
    spec = importlib.util.spec_from_file_location("build_installer", ROOT / "scripts" / "build_installer.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_installer"] = module
    spec.loader.exec_module(module)
    return module


def test_a_shipped_wrapper_holds_no_date_from_the_build_pc(tmp_path: Path) -> None:
    wrapper = tmp_path / "_944DE083_0_1_0.py"
    wrapper.write_text(
        "typelib_path = 'C:\\\\Windows\\\\System32\\\\UIAutomationCore.dll'\n"
        "__all__ = ['typelib_path']\n"
        "_check_version('1.4.17', 1793256891.526859)\n",
        encoding="utf-8",
    )
    build = _build()
    assert build.any_pc(wrapper)
    text = wrapper.read_text(encoding="utf-8")
    assert "1793256891" not in text
    assert "_check_version('1.4.17', _made_from())" in text
    assert not build.any_pc(wrapper)  # once is enough

    seen: list[tuple[str, float]] = []
    scope = {"_check_version": lambda version, made: seen.append((version, made))}
    exec(text.replace("C:\\\\Windows\\\\System32\\\\UIAutomationCore.dll", "Z:/not/here.dll"), scope)
    assert seen == [("1.4.17", 0.0)]  # no such file on this PC: no error, and comtypes lets it be


def test_a_friendly_wrapper_with_no_date_is_left_alone(tmp_path: Path) -> None:
    friendly = tmp_path / "stdole.py"
    friendly.write_text("from comtypes.gen import _00020430 as __wrapper_module__\n", encoding="utf-8")
    assert not _build().any_pc(friendly)
    assert friendly.read_text(encoding="utf-8").startswith("from comtypes.gen")
