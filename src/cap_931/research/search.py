"""Web and news search. Uses Tavily when TAVILY_API_KEY is set, otherwise DuckDuckGo (no key needed)."""

import logging
import threading
import time
from email.utils import parsedate_to_datetime

import httpx
from ddgs import DDGS

from ..cache import cached
from ..config import get_settings

log = logging.getLogger(__name__)

_ddg_slots = threading.Semaphore(2)


def iso_date(raw: str) -> str:
    """'Thu, 06 Nov 2025 17:00:00 GMT' or '2025-11-06T17:00:00' -> '2025-11-06'."""
    raw = (raw or "").strip()
    if not raw or raw[:4].isdigit():
        return raw[:10]
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return ""


def _tavily(query: str, max_results: int, news: bool) -> list[dict]:
    payload = {
        "api_key": get_settings().tavily_api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "topic": "news" if news else "general",
        "include_raw_content": True,
    }
    if news:
        payload["days"] = 365
    try:
        resp = httpx.post("https://api.tavily.com/search", json=payload, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("Tavily search failed for %r: %s", query, exc)
        return []
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
            "content": r.get("raw_content") or "",
            "published": iso_date(r.get("published_date", "")),
        }
        for r in resp.json().get("results", [])
    ]


def _ddg_once(query: str, max_results: int, news: bool) -> list[dict]:
    if news:
        raw = DDGS().news(query, max_results=max_results, timelimit="y")
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("body", ""),
             "content": "", "published": iso_date(r.get("date", ""))}
            for r in raw
        ]
    raw = DDGS().text(query, max_results=max_results)
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", ""),
         "content": "", "published": ""}
        for r in raw
    ]


def _duckduckgo(query: str, max_results: int, news: bool) -> list[dict]:
    # Retry once, then fall back to a looser query without quotes or OR operators.
    loose = query.replace('"', "").replace(" OR ", " ")
    for attempt, q in enumerate([query, query, loose]):
        with _ddg_slots:
            try:
                results = _ddg_once(q, max_results, news)
            except Exception as exc:  # ddgs raises its own types for "no results", rate limits and timeouts
                log.info("DuckDuckGo attempt %d failed for %r: %s", attempt + 1, q, exc)
                results = []
        if results:
            return results
        time.sleep(1.5)
    log.warning("DuckDuckGo found nothing for %r", query)
    return []


def search(query: str, max_results: int = 5, news: bool = False, use_cache: bool = True) -> list[dict]:
    """Return results as dicts with title, url, snippet, content (may be empty) and published."""
    provider = get_settings().search_provider
    run = _tavily if provider == "tavily" else _duckduckgo
    if not use_cache:
        return run(query, max_results, news)
    return cached(f"search-{provider}", f"{news}:{max_results}:{query}", lambda: run(query, max_results, news))
