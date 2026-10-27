import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ..config import get_settings
from ..schemas import CompanyProfile, Evidence, Intake, SalesInput
from . import edgar
from .fetch import domain_of, fetch_html, fetch_text, internal_links, meta_description, normalize_url, site_name
from .search import search

log = logging.getLogger(__name__)

Progress = Callable[[str], None]

SITE_PAGES = {"leadership": "leadership", "about": "company_site", "news": "company_site",
              "investors": "company_site", "careers": "jobs"}
MAX_FULLTEXT_FETCHES = 24


def profile_company(url: str) -> CompanyProfile:
    url = normalize_url(url)
    page = fetch_html(url)
    if not page:
        return CompanyProfile(name=domain_of(url).split(".")[0].capitalize(), domain=domain_of(url), url=url)
    text = fetch_text(url).get("text", "")
    description = meta_description(page["html"]) or text[:400]
    return CompanyProfile(name=site_name(page["html"], page["url"]), domain=domain_of(url), url=page["url"],
                          description=description)


def short_name(company: CompanyProfile) -> str:
    """'Snowflake AI Data Cloud' -> 'Snowflake': prefer the brand word from the domain when it appears in the name."""
    stem = company.domain.split(".")[0]
    for word in company.name.split():
        if word.lower() == stem.lower():
            return word
    return company.name


def match_terms(company: CompanyProfile) -> list[str]:
    """Lowercase strings whose presence means a search result is actually about the company."""
    terms = {company.name.lower(), company.domain.split(".")[0].lower()}
    core = edgar.SUFFIXES.sub(" ", company.name.lower())
    core = re.sub(r"\s+", " ", core).strip(" ,.")
    if len(core) > 2:
        terms.add(core)
    return sorted(t for t in terms if len(t) > 2)


def focus_text(text: str, terms: list[str], limit: int) -> str:
    """Trim long text to `limit` chars, keeping paragraphs that mention the terms first."""
    text = text.strip()
    if len(text) <= limit:
        return text
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    hits = [p for p in paragraphs if any(t in p.lower() for t in terms)]
    rest = [p for p in paragraphs if p not in hits]
    out, size = [], 0
    for p in hits + rest:
        if size + len(p) > limit:
            if not out:
                out.append(p[:limit])
            break
        out.append(p)
        size += len(p) + 1
    return "\n".join(out)


def _search_plan(company: CompanyProfile, intake: Intake, inputs: SalesInput,
                 competitors: list[CompanyProfile]) -> list[tuple[str, str, bool]]:
    """(query, source_type, is_news) tuples covering every section of the one-pager."""
    name, category = short_name(company), intake.category
    keyword = intake.search_keywords[0] if intake.search_keywords else category
    plan = [
        (f'"{name}" {category}', "news", True),
        (f'"{name}" announces {keyword}', "news", True),
        (f'"{name}" {category} strategy', "web", False),
        (f'"{name}" chief data officer OR CTO OR CIO {keyword}', "news", False),
        (f'"{name}" leadership team executives', "leadership", False),
        (f'"{name}" jobs {category} engineer', "jobs", False),
        (f'"{name}" annual report {category}', "web", False),
    ]
    if inputs.target_customer:
        plan.append((f'"{inputs.target_customer}" "{name}"', "leadership", False))
    for comp in competitors:
        plan.append((f'"{name}" "{short_name(comp)}"', "web", False))
        plan.append((f'"{name}" {short_name(comp)} customer', "news", True))
    return plan


def gather_evidence(inputs: SalesInput, intake: Intake, progress: Progress = lambda _: None
                    ) -> tuple[CompanyProfile, list[CompanyProfile], list[Evidence], list[str]]:
    settings = get_settings()
    warnings: list[str] = []
    raw: list[dict] = []  # {"title", "url", "source_type", "text", "published"} before numbering

    progress("Reading the prospect's website")
    company = profile_company(inputs.company_url)
    home = fetch_html(company.url)
    if not home:
        warnings.append(f"Could not load {company.url}; relying on search results only.")

    with ThreadPoolExecutor(max_workers=8) as pool:
        competitors = list(pool.map(profile_company, inputs.competitor_urls))
        for comp in competitors:  # "Snowflake AI Data Cloud" -> "Snowflake" for searching and display
            comp.name = short_name(comp)
        terms = match_terms(company)

        if home:
            home_text = fetch_text(company.url)
            if home_text:
                raw.append({**home_text, "source_type": "company_site", "published": ""})
            links = internal_links(home["html"], home["url"])
            targets = [(url, SITE_PAGES[label]) for label in SITE_PAGES for url in links.get(label, [])[:1]]
            if len(links.get("leadership", [])) > 1:
                targets.append((links["leadership"][1], "leadership"))
            for (url, kind), page in zip(targets, pool.map(lambda t: fetch_text(t[0]), targets)):
                if page:
                    raw.append({**page, "source_type": kind, "published": ""})

        progress(f"Searching the web ({settings.search_provider}) for news, executives, jobs and competitors")
        plan = _search_plan(company, intake, inputs, competitors)
        results = pool.map(lambda q: search(q[0], max_results=6, news=q[2]), plan)
        competitor_terms = [t for c in competitors for t in match_terms(c)]
        seen = {r["url"].rstrip("/") for r in raw}
        found: list[dict] = []
        for (query, kind, _), hits in zip(plan, results):
            for rank, hit in enumerate(hits):
                url = hit["url"].rstrip("/")
                blob = f"{hit['title']} {hit['snippet']} {hit['content'][:5000]}".lower()
                # Drop results that never mention the prospect (search engines pad with loose matches).
                if not url or url in seen or not any(t in blob for t in terms):
                    continue
                seen.add(url)
                found.append({"title": hit["title"], "url": hit["url"], "source_type": kind,
                              "text": hit["content"] or hit["snippet"], "published": hit["published"],
                              "rank": rank})

        progress(f"Reading {min(len(found), MAX_FULLTEXT_FETCHES)} articles in full")
        # Read the top hits of every query before the second-best hits of any query.
        needs_text = sorted((f for f in found if len(f["text"]) < 600), key=lambda f: f["rank"])[:MAX_FULLTEXT_FETCHES]
        for item, page in zip(needs_text, pool.map(lambda f: fetch_text(f["url"]), needs_text)):
            if page and len(page["text"]) > len(item["text"]):
                item["text"] = page["text"]
        raw.extend(found)

    progress("Checking SEC EDGAR for a 10-K")
    try:
        filing = edgar.tenk_excerpts(company.name, company.domain, [intake.category, *intake.search_keywords])
    except Exception as exc:  
        log.warning("EDGAR lookup failed: %s", exc)
        filing = None
    if filing:
        parts = [f"BUSINESS OVERVIEW: {filing['business']}", f"MD&A: {filing['mdna']}",
                 f"PASSAGES RELEVANT TO {intake.category.upper()}: {filing['relevant']}"]
        raw.append({"title": filing["title"], "url": filing["url"], "source_type": "filing",
                    "text": "\n\n".join(p for p in parts if not p.endswith(": ")), "published": filing["filed"]})
    else:
        warnings.append("No SEC 10-K found (company may be private or listed outside the US).")

    all_terms = terms + competitor_terms + [inputs.target_customer.lower()] * bool(inputs.target_customer)
    evidence = []
    for item in raw:
        limit = settings.max_evidence_chars * (4 if item["source_type"] == "filing" else 1)
        text = focus_text(item["text"], all_terms, limit)
        if len(text) < 80:
            continue
        evidence.append(Evidence(id=len(evidence) + 1, title=item["title"][:200], url=item["url"],
                                 source_type=item["source_type"], text=text, published=item["published"]))
    if len(evidence) < 5:
        warnings.append(f"Only {len(evidence)} sources found; the one-pager may be thin.")
    return company, competitors, evidence, warnings
