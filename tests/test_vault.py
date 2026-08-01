"""Behavior tests for the read-only Obsidian vault reader.

The app must work identically with no vault at all, so every accessor
degrades to None/[] rather than raising.
"""
from __future__ import annotations

import pytest

from app import vault


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(vault, "_articles_cache", None)
    (tmp_path / "Stocks").mkdir()
    (tmp_path / "Sectors").mkdir()
    (tmp_path / "Articles").mkdir()
    return tmp_path


# --------------------------------------------------------------------------
# frontmatter parsing
# --------------------------------------------------------------------------

def test_flat_frontmatter_keys_are_parsed():
    meta, body = vault._split_frontmatter(
        "---\ntitle: NVDA\nupdated: 2026-06-10\n---\n\n# NVDA\n\nBody text.")
    assert meta["title"] == "NVDA"
    assert meta["updated"] == "2026-06-10"
    assert body.startswith("# NVDA")


def test_the_prices_mapping_is_parsed_as_numbers():
    meta, _ = vault._split_frontmatter(
        "---\ntitle: A\nprices:\n  NVDA: 200.42\n  AAOI: 175.13\n---\nbody")
    assert meta["prices"] == {"NVDA": 200.42, "AAOI": 175.13}


def test_trailing_comments_are_stripped_from_mapped_values():
    meta, _ = vault._split_frontmatter(
        "---\nprices:\n  NVDA: 200.42  # close on publication day\n---\nbody")
    assert meta["prices"]["NVDA"] == 200.42


def test_a_non_numeric_mapped_value_survives_as_text():
    meta, _ = vault._split_frontmatter("---\nprices:\n  NVDA: unknown\n---\nbody")
    assert meta["prices"]["NVDA"] == "unknown"


@pytest.mark.parametrize("text", [
    "no frontmatter at all",
    "---\nunterminated: true\n",          # no closing fence
    "---\n---\n",                          # empty block
    "",
])
def test_malformed_frontmatter_never_raises(text):
    meta, body = vault._split_frontmatter(text)
    assert isinstance(meta, dict) and isinstance(body, str)


# --------------------------------------------------------------------------
# notes
# --------------------------------------------------------------------------

def test_stock_note_reads_the_body_and_updated_date(vault_dir):
    (vault_dir / "Stocks" / "NVDA.md").write_text(
        "---\nupdated: 2026-06-10\n---\n\n# NVDA\n\n## Facts\n- fast", encoding="utf-8")
    note = vault.stock_note("nvda")           # lookup is case-insensitive
    assert note["updated"] == "2026-06-10"
    assert "## Facts" in note["note_md"]


def test_missing_notes_return_none(vault_dir):
    assert vault.stock_note("ZZZZ") is None
    assert vault.sector_note("Nonexistent") is None


def test_an_empty_note_is_treated_as_absent(vault_dir):
    (vault_dir / "Stocks" / "EMPTY.md").write_text("---\nupdated: x\n---\n", encoding="utf-8")
    assert vault.stock_note("EMPTY") is None, "frontmatter with no body is not a note"


def test_sector_note_is_looked_up_by_name(vault_dir):
    (vault_dir / "Sectors" / "Semiconductors.md").write_text(
        "# Semiconductors\n\nThe sector note.", encoding="utf-8")
    assert "sector note" in vault.sector_note("Semiconductors")["note_md"]


# --------------------------------------------------------------------------
# articles
# --------------------------------------------------------------------------

def write_article(vault_dir, name, tickers, prices_block="", published="2026-06-10"):
    (vault_dir / "Articles" / f"{name}.md").write_text(
        f"---\ntitle: {name} headline\nurl: http://example.com/{name}\n"
        f"published: {published}\ntickers: {tickers}\n{prices_block}---\n\nBody.",
        encoding="utf-8")


def test_articles_are_matched_to_their_tickers(vault_dir):
    write_article(vault_dir, "optics", "AAOI, NVDA",
                  "prices:\n  AAOI: 175.13\n  NVDA: 200.42\n")
    write_article(vault_dir, "biotech", "IKT", "prices:\n  IKT: 2.10\n")

    aaoi = vault.articles_for("AAOI")
    assert len(aaoi) == 1
    assert aaoi[0]["price_at"] == 175.13
    assert aaoi[0]["url"] == "http://example.com/optics"
    assert [a["title"] for a in vault.articles_for("IKT")] == ["biotech headline"]
    assert vault.articles_for("MSFT") == []


def test_ticker_lists_accept_semicolons_and_odd_spacing(vault_dir):
    write_article(vault_dir, "mixed", " nvda ;msft , tsm ")
    for ticker in ("NVDA", "MSFT", "TSM"):
        assert len(vault.articles_for(ticker)) == 1, f"{ticker} should match"


def test_an_article_without_a_price_still_lists(vault_dir):
    write_article(vault_dir, "nopricing", "NVDA")
    article = vault.articles_for("NVDA")[0]
    assert article["price_at"] is None, "a missing price must not drop the article"


def test_articles_are_newest_first(vault_dir):
    write_article(vault_dir, "older", "NVDA", published="2026-01-01")
    write_article(vault_dir, "newer", "NVDA", published="2026-06-01")
    assert [a["published"] for a in vault.articles_for("NVDA")] == ["2026-06-01", "2026-01-01"]


def test_the_article_cache_notices_a_new_file(vault_dir):
    write_article(vault_dir, "first", "NVDA")
    assert len(vault.articles_for("NVDA")) == 1
    write_article(vault_dir, "second", "NVDA")
    assert len(vault.articles_for("NVDA")) == 2, "edits in Obsidian appear without a restart"


# --------------------------------------------------------------------------
# status / absence
# --------------------------------------------------------------------------

def test_status_counts_each_folder(vault_dir):
    (vault_dir / "Stocks" / "NVDA.md").write_text("# NVDA\nbody", encoding="utf-8")
    (vault_dir / "Sectors" / "Semis.md").write_text("# Semis\nbody", encoding="utf-8")
    write_article(vault_dir, "a", "NVDA")
    status = vault.status()
    assert status["available"] is True
    assert (status["stocks"], status["sectors"], status["articles"]) == (1, 1, 1)


def test_everything_degrades_when_the_vault_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "VAULT_PATH", tmp_path / "does-not-exist")
    monkeypatch.setattr(vault, "_articles_cache", None)
    assert vault.available() is False
    assert vault.status()["available"] is False
    assert vault.stock_note("NVDA") is None
    assert vault.articles_for("NVDA") == []
