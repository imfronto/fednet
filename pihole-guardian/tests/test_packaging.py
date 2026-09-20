"""Guards against the exact failure mode that reached a real deployment
once already: `pip install -e .` (what local dev/testing uses) resolves
straight back to the source tree, so a non-Python file missing from
`[tool.setuptools.package-data]` in pyproject.toml is invisible in every
test and every local smoke check — it only breaks on a real, non-editable
install, which is exactly what the Dockerfile does. This test performs a
real (non-editable) install into a scratch directory and diffs the result
against the source tree, so a forgotten resource file fails the test suite
instead of failing silently in production.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PACKAGE_ROOT = PROJECT_ROOT / "src" / "pihole_guardian"


def _non_python_source_files() -> set[str]:
    return {
        str(p.relative_to(PACKAGE_ROOT))
        for p in PACKAGE_ROOT.rglob("*")
        if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
    }


def test_every_non_python_file_survives_a_real_install(tmp_path):
    target = tmp_path / "installed"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--target", str(target), str(PROJECT_ROOT)],
        check=True,
        capture_output=True,
        text=True,
    )
    installed_root = target / "pihole_guardian"
    installed_files = {
        str(p.relative_to(installed_root))
        for p in installed_root.rglob("*")
        if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
    }

    source_files = _non_python_source_files()
    missing = source_files - installed_files
    assert not missing, (
        f"These files exist in src/pihole_guardian/ but are missing from a real install "
        f"(add them to [tool.setuptools.package-data] in pyproject.toml): {sorted(missing)}"
    )
