import logging
import re

import httpx
from lxml import html as lxml_html

from ..cache import cached
from ..config import get_settings

log = logging.getLogger(__name__)

SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|plc|group|holdings?|the|n\.?a|s\.?a|ag|se|nv)\b\.?",
    re.I,
)


def _normalize(name: str) -> str:
    name = SUFFIXES.sub(" ", name.lower().replace("&", " and "))
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def _get(url: str) -> httpx.Response | None:
    try:
        resp = httpx.get(url, headers={"User-Agent": get_settings().sec_user_agent}, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp
    except httpx.HTTPError as exc:
        log.warning("EDGAR request failed for %s: %s", url, exc)
        return None


def _tickers() -> list[dict]:
    def load():
        resp = _get("https://www.sec.gov/files/company_tickers.json")
        return list(resp.json().values()) if resp else []

    return cached("edgar", "company_tickers", load)


def find_cik(company_name: str, domain: str = "") -> dict | None:
    """Match a company to an SEC registrant by normalized name. Returns {"cik", "title", "ticker"}."""
    target = _normalize(company_name)
    stem = _normalize(domain.split(".")[0]) if domain else ""
    if not target and not stem:
        return None
    entries = _tickers()
    for candidate in (target, stem):
        if not candidate:
            continue
        for e in entries:  # exact normalized match first
            if _normalize(e["title"]) == candidate:
                return {"cik": int(e["cik_str"]), "title": e["title"], "ticker": e["ticker"]}
    for e in entries:  # then a whole-word prefix match ("capital one" -> "capital one financial")
        if target and len(target) > 3 and _normalize(e["title"]).startswith(target + " "):
            return {"cik": int(e["cik_str"]), "title": e["title"], "ticker": e["ticker"]}
    return None


def latest_10k_url(cik: int) -> tuple[str, str] | None:
    resp = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if not resp:
        return None
    recent = resp.json().get("filings", {}).get("recent", {})
    for form, accession, doc, date in zip(
        recent.get("form", []), recent.get("accessionNumber", []),
        recent.get("primaryDocument", []), recent.get("filingDate", []),
    ):
        if form == "10-K":
            return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}", date
    return None


def _section(text: str, start: str, end: str, limit: int) -> str:
    """Find the real section body between two item headings.

    The first heading match is usually the table of contents (the next heading follows within a few
    hundred characters), and later matches are cross-references, so take the first long section.
    """
    for match in re.finditer(start, text, re.I):
        tail = text[match.end():]
        stop = re.search(end, tail, re.I)
        body = tail[: stop.start()] if stop else tail[:limit]
        if len(body) > 3000:
            return re.sub(r"\s+", " ", body).strip()[:limit]
    return ""


def _relevant_paragraphs(text: str, keywords: list[str], limit: int) -> str:
    words = {w.lower() for kw in keywords for w in re.findall(r"[A-Za-z]{4,}", kw)}
    if not words:
        return ""
    paragraphs = [p for p in text.split("\n") if 200 < len(p) < 3000]

    def score(p: str) -> int:
        lower = p.lower()
        return sum(1 for w in words if w in lower) * 10 + sum(lower.count(w) for w in words)

    picked = [p for p in sorted(paragraphs, key=score, reverse=True)[:6] if score(p) >= 20]
    return "\n\n".join(re.sub(r"\s+", " ", p) for p in picked)[:limit]


def tenk_excerpts(company_name: str, domain: str, keywords: list[str]) -> dict | None:
    """Return {"title", "url", "filed", "business", "mdna", "relevant"} or None if not a US-listed company."""
    match = find_cik(company_name, domain)
    if not match:
        return None
    found = latest_10k_url(match["cik"])
    if not found:
        return None
    url, filed = found

    def load():
        resp = _get(url)
        if not resp:
            return {}
        tree = lxml_html.fromstring(resp.content)
        for hidden in tree.xpath("//*[local-name()='header']|//script|//style"):
            hidden.drop_tree()
        # Keep block boundaries so the text can be split into paragraphs.
        for block in tree.iter("p", "div", "tr", "li", "br", "h1", "h2", "h3", "h4"):
            block.tail = "\n" + (block.tail or "")
        text = re.sub(r"\n\s*\n+", "\n", tree.text_content())
        return {
            "business": _section(text, r"item\s*1\.?\s*business", r"item\s*1a\.?\s*risk", 6000),
            "mdna": _section(text, r"item\s*7\.?\s*management", r"item\s*7a\.?|item\s*8\.?", 6000),
            "relevant": _relevant_paragraphs(text, keywords, 5000),
        }

    excerpts = cached("edgar-10k", f"{url}:{','.join(keywords)}", load)
    if not excerpts:
        return None
    return {"title": f"{match['title']} Form 10-K (filed {filed})", "url": url, "filed": filed, **excerpts}
