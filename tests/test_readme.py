from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_actual_build_workflow() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "uv sync" in text
    assert "build_site --source" in text
    assert "python -m http.server" in text
    assert "--base-path /yt-insights/" in text
    assert "private" in text.lower()


def test_data_contract_exists() -> None:
    text = (ROOT / "docs" / "data-contract.md").read_text(encoding="utf-8")

    assert "data/corpus.json" in text
    assert "data/videos" in text
    assert "verification_status" in text
