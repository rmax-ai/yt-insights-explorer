from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "corpus"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "yt_insights_web.cli", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def test_help_is_available() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "--source" in result.stdout
    assert "--out" in result.stdout


def test_source_is_required() -> None:
    result = run_cli()

    assert result.returncode != 0
    assert "--source" in result.stderr


def test_default_output_is_site(tmp_path: Path) -> None:
    result = run_cli("--source", str(FIXTURE), "--site-title", "Fixture")

    assert result.returncode == 0
    assert (ROOT / "site").is_dir()


def test_bad_source_fails_without_creating_output(tmp_path: Path) -> None:
    output = tmp_path / "output"
    result = run_cli("--source", str(tmp_path / "missing"), "--out", str(output))

    assert result.returncode != 0
    assert not output.exists()
