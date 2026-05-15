from pathlib import Path

from concord.retrieval.chunking import chunk_markdown


def _write(tmp: Path, text: str) -> Path:
    p = tmp / "doc.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_splits_by_headings(tmp_path: Path) -> None:
    md = "# Top\n\nIntro.\n\n## Sub A\n\nbody A\n\n## Sub B\n\nbody B"
    chunks = chunk_markdown(_write(tmp_path, md), max_chars=400, overlap=50)
    trails = [c.heading_trail for c in chunks]
    assert "Top" in trails
    assert "Top > Sub A" in trails
    assert "Top > Sub B" in trails


def test_long_section_is_packed(tmp_path: Path) -> None:
    body = ("paragraph one with some words.\n\n" * 80)
    chunks = chunk_markdown(_write(tmp_path, f"# H\n\n{body}"), max_chars=300, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 320  # allow tiny slack
