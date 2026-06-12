"""Read-only access to the Obsidian "Stocks Vault" (Karpathy LLM-wiki layout).

The app only ever READS the vault — Claude sessions and the user (via
Obsidian) are the writers. Everything here degrades to None/[] when the
vault, a note, or a frontmatter field is missing, so the app works
identically with no vault at all.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

VAULT_PATH = Path(os.environ.get("STOCKS_VAULT", "~/Documents/Stocks Vault")).expanduser()

_lock = threading.Lock()
_articles_cache: Optional[tuple[int, list[dict]]] = None  # (scan signature, parsed notes)


def available() -> bool:
    return VAULT_PATH.is_dir()


def status() -> dict:
    """Note counts for the DB view's status panel."""
    def count(folder: str) -> int:
        path = VAULT_PATH / folder
        return len(list(path.glob("*.md"))) if path.is_dir() else 0

    return {
        "path": str(VAULT_PATH),
        "available": available(),
        "stocks": count("Stocks"),
        "sectors": count("Sectors"),
        "articles": count("Articles"),
    }


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Parse a minimal YAML subset: flat `key: value` lines plus optional
    one-level mappings (a `key:` line followed by indented `k: v` lines —
    used for `prices:`). Returns ({}, full text) when frontmatter is absent
    or malformed; never raises."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text

    meta: dict = {}
    current_map: Optional[str] = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        line = raw.strip()
        if raw[:1] in (" ", "\t") and current_map is not None and ":" in line:
            key, _, value = line.partition(":")
            value = value.split("#")[0].strip()
            try:
                meta[current_map][key.strip()] = float(value)
            except ValueError:
                meta[current_map][key.strip()] = value
            continue
        current_map = None
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value == "":
            meta[key] = {}
            current_map = key
        else:
            meta[key] = value
    return meta, "\n".join(lines[end + 1:]).strip()


def _read_note(path: Path) -> Optional[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _split_frontmatter(text)
    return {"meta": meta, "body": body}


def _note_payload(path: Path) -> Optional[dict]:
    note = _read_note(path)
    if note is None or not note["body"]:
        return None
    return {"note_md": note["body"], "updated": note["meta"].get("updated")}


def stock_note(ticker: str) -> Optional[dict]:
    return _note_payload(VAULT_PATH / "Stocks" / f"{ticker.upper()}.md")


def sector_note(name: str) -> Optional[dict]:
    return _note_payload(VAULT_PATH / "Sectors" / f"{name}.md")


def _scan_articles() -> list[dict]:
    """All article notes with parsed metadata, cached against a directory
    signature so vault edits show up without restarting the server."""
    global _articles_cache
    articles_dir = VAULT_PATH / "Articles"
    if not articles_dir.is_dir():
        return []

    entries = []
    for path in sorted(articles_dir.glob("*.md")):
        try:
            entries.append((path, path.stat().st_mtime_ns))
        except OSError:
            continue
    signature = hash(tuple((p.name, m) for p, m in entries))
    with _lock:
        if _articles_cache and _articles_cache[0] == signature:
            return _articles_cache[1]

    parsed = []
    for path, _ in entries:
        note = _read_note(path)
        if note is None:
            continue
        meta = note["meta"]
        tickers = [t.strip().upper()
                   for t in str(meta.get("tickers", "")).replace(";", ",").split(",")
                   if t.strip()]
        raw_prices = meta.get("prices")
        prices = ({k.upper(): v for k, v in raw_prices.items()
                   if isinstance(v, (int, float))}
                  if isinstance(raw_prices, dict) else {})
        parsed.append({
            "file": path.stem,
            "title": str(meta.get("title") or path.stem),
            "url": meta.get("url"),
            "published": meta.get("published"),
            "tickers": tickers,
            "prices": prices,
        })
    parsed.sort(key=lambda a: str(a.get("published") or ""), reverse=True)

    with _lock:
        _articles_cache = (signature, parsed)
    return parsed


def articles_for(ticker: str) -> list[dict]:
    symbol = ticker.upper()
    return [
        {"title": a["title"], "url": a["url"], "published": a["published"],
         "price_at": a["prices"].get(symbol)}
        for a in _scan_articles() if symbol in a["tickers"]
    ]
