import json
import re
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from lxml import html as lxml_html

from ..cache import cached

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Internal pages worth reading on a prospect's site, keyed by how we label them.
PAGE_PATTERNS = {
    "leadership": re.compile(r"leadership|management|executive|our-team|/team|board-of-directors|officers", re.I),
    "about": re.compile(r"about|who-we-are|company|our-story|mission", re.I),
    "news": re.compile(r"news|press|newsroom|media|blog|insights|stories", re.I),
    "investors": re.compile(r"investor|annual-report|ir\.", re.I),
    "careers": re.compile(r"career|jobs|join-us", re.I),
}


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def domain_of(url: str) -> str:
    host = urlparse(normalize_url(url)).netloc.lower()
    return host.removeprefix("www.")


def _download(url: str) -> dict:
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            resp = client.get(url)
        content_type = resp.headers.get("content-type", "")
        if resp.status_code >= 400 or "html" not in content_type:
            return {}
        return {"url": str(resp.url), "html": resp.text}
    except httpx.HTTPError:
        return {}


def fetch_html(url: str) -> dict:
    """Return {"url", "html"} for a page, or {} if it can't be fetched."""
    return cached("html", url, lambda: _download(url))


def extract_text(page_html: str, url: str = "") -> str:
    text = trafilatura.extract(page_html, url=url or None, include_comments=False, include_tables=True)
    if text:
        return text
    try:
        tree = lxml_html.fromstring(page_html)
        for bad in tree.xpath("//script|//style|//noscript"):
            bad.drop_tree()
        return re.sub(r"\s+", " ", tree.text_content()).strip()
    except (lxml_html.etree.ParserError, ValueError):
        return ""


def page_title(page_html: str) -> str:
    try:
        tree = lxml_html.fromstring(page_html)
    except (lxml_html.etree.ParserError, ValueError):
        return ""
    for xpath in ("//meta[@property='og:site_name']/@content", "//meta[@property='og:title']/@content", "//title/text()"):
        found = tree.xpath(xpath)
        if found and found[0].strip():
            return found[0].strip()
    return ""


def site_name(page_html: str, url: str) -> str:
    """Best guess at a company's name from its homepage."""
    try:
        tree = lxml_html.fromstring(page_html)
        for block in tree.xpath("//script[@type='application/ld+json']/text()"):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else data.get("@graph", [data])
            for item in items:
                if isinstance(item, dict) and item.get("@type") in ("Organization", "Corporation") and item.get("name"):
                    return str(item["name"]).strip()
        og = tree.xpath("//meta[@property='og:site_name']/@content")
        if og and og[0].strip():
            return og[0].strip()
        title = tree.xpath("//title/text()")
        if title and title[0].strip():
            parts = [p.strip() for p in re.split(r"\s[|\-–—:·]\s", title[0]) if p.strip()]
            stem = domain_of(url).split(".")[0].lower()
            for part in parts:
                if stem in part.lower().replace(" ", ""):
                    return part
            if parts:
                return min(parts, key=len)
    except (lxml_html.etree.ParserError, ValueError):
        pass
    return domain_of(url).split(".")[0].capitalize()


def meta_description(page_html: str) -> str:
    try:
        tree = lxml_html.fromstring(page_html)
    except (lxml_html.etree.ParserError, ValueError):
        return ""
    for xpath in ("//meta[@name='description']/@content", "//meta[@property='og:description']/@content"):
        found = tree.xpath(xpath)
        if found and found[0].strip():
            return found[0].strip()
    return ""


def internal_links(page_html: str, base_url: str) -> dict[str, list[str]]:
    """Group same-site links by the PAGE_PATTERNS category they look like."""
    try:
        tree = lxml_html.fromstring(page_html)
    except (lxml_html.etree.ParserError, ValueError):
        return {}
    base_domain = domain_of(base_url)
    groups: dict[str, list[str]] = {k: [] for k in PAGE_PATTERNS}
    seen = set()
    for href in tree.xpath("//a/@href"):
        absolute = urljoin(base_url, href).split("#")[0].rstrip("/")
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or absolute in seen:
            continue
        # Allow subdomains such as investor.example.com.
        if not parsed.netloc.lower().removeprefix("www.").endswith(base_domain):
            continue
        seen.add(absolute)
        for label, pattern in PAGE_PATTERNS.items():
            if pattern.search(parsed.path) or pattern.search(parsed.netloc):
                groups[label].append(absolute)
                break
    return {k: sorted(v, key=len)[:3] for k, v in groups.items() if v}


def fetch_text(url: str) -> dict:
    """Return {"url", "title", "text"} for a page, or {} if it can't be fetched."""
    page = fetch_html(url)
    if not page:
        return {}
    text = extract_text(page["html"], page["url"])
    if not text:
        return {}
    return {"url": page["url"], "title": page_title(page["html"]) or page["url"], "text": text}
