from pathlib import Path

from knowledge_ops.metrics import record_metric, render_metric_chart
from knowledge_ops.text import chunk_text, lexical_score
from knowledge_ops.wiki import search_wiki, upsert_doc


def test_chunk_text_overlaps_long_text():
    chunks = chunk_text("alpha " * 500, max_chars=200, overlap=20)
    assert len(chunks) > 1
    assert all(chunks)


def test_lexical_score_matches_title_and_body():
    assert lexical_score("Magic Unicorn sovereign AI", "sovereign AI you own", "Magic Unicorn") > 0
    assert lexical_score("unrelated", "sovereign AI you own", "Magic Unicorn") == 0


def test_wiki_upsert_and_search(tmp_path, monkeypatch):
    import knowledge_ops.config as config
    import knowledge_ops.storage as storage
    import knowledge_ops.wiki as wiki

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "knowledge.sqlite3")
    monkeypatch.setattr(storage.settings, "data_dir", tmp_path)
    monkeypatch.setattr(storage.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(storage.settings, "db_path", tmp_path / "knowledge.sqlite3")
    monkeypatch.setattr(wiki.settings, "wiki_dir", tmp_path / "wiki")

    doc_id = upsert_doc("test/doc.md", "# Test Doc\n\nMagic Unicorn builds sovereign AI.")
    hits = search_wiki("sovereign AI", k=3)
    assert doc_id
    assert hits
    assert hits[0].source == "wiki"


def test_metric_chart(tmp_path, monkeypatch):
    import knowledge_ops.config as config
    import knowledge_ops.storage as storage

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "metrics.sqlite3")
    monkeypatch.setattr(storage.settings, "data_dir", tmp_path)
    monkeypatch.setattr(storage.settings, "db_path", tmp_path / "metrics.sqlite3")

    record_metric("latency", 3.6, {"model": "qwen"})
    svg = render_metric_chart("latency")
    assert "<svg" in svg
    assert "latency" in svg
