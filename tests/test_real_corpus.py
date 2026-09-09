from __future__ import annotations

import os
from pathlib import Path

import pytest

from yt_insights_web.build import build_site
from yt_insights_web.verify import verify_site


@pytest.mark.real_corpus
def test_real_corpus_build_is_read_only(tmp_path: Path) -> None:
    source = os.environ.get("YT_INSIGHTS_SOURCE")
    if not source:
        pytest.skip("set YT_INSIGHTS_SOURCE to run the private corpus acceptance test")

    output = tmp_path / "site"
    build_site(source, output)
    report = verify_site(output)

    assert report.video_count == 73
