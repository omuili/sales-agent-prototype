import io

from docx.shared import Pt

from . import layout
from . import template as t
from .agents import all_claims
from .config import get_settings
from .research.fetch import domain_of
from .schemas import Claim, Leader, OnePager

PARTIAL_MARK = "⚠"
SOURCE_LABELS = {"company_site": "Company website", "news": "News", "jobs": "Jobs", "leadership": "Leadership",
                 "filing": "SEC filing", "competitor_site": "Competitor site", "web": "Web"}


def claims_of(pager: OnePager) -> list[Claim | Leader]:
    return all_claims(pager.brief, pager.strategy, pager.competitor_section, pager.leadership, pager.financials)


def cited_ids(pager: OnePager) -> list[int]:
    return sorted({c for item in claims_of(pager) for c in item.citations})


def _cite_md(item: Claim | Leader, urls: dict[int, str]) -> str:
    refs = "".join(f"[[{c}]]({urls[c]})" for c in item.citations if c in urls)
    flag = f" {PARTIAL_MARK}" if item.verdict == "partial" else ""
    return f" {refs}{flag}" if refs or flag else ""


def _claims_md(claims: list[Claim], urls: dict[int, str], empty: str) -> list[str]:
    if not claims:
        return [f"- _{empty}_"]
    return [f"- {c.text}{_cite_md(c, urls)}" for c in claims]


def _brief_sections(pager: OnePager) -> list[tuple[str, list]]:
    return layout.sections(pager.brief)


def brief_markdown(pager: OnePager) -> str:
    """The one-page brief (what the .docx prints)."""
    urls = {e.id: e.url for e in pager.sources}
    p, out = pager, []
    out.append(f"# Account Brief: {p.company.name}")
    out.append(f"*Selling **{p.inputs.product_name}** ({p.intake.category}) · {p.company.url} · "
               f"generated {p.generated_at:%B %d, %Y}*\n")
    out.append(f"**{p.brief.headline}**")
    for title, items in _brief_sections(p):
        if not items:
            continue
        out.append(f"\n### {title}")
        for item in items:
            if isinstance(item, Leader):
                out.append(f"- **{item.name}**, {item.title}: {item.relevance}{_cite_md(item, urls)}")
            else:
                out.append(f"- {item.text}{_cite_md(item, urls)}")
    return "\n".join(out)


def research_markdown(pager: OnePager) -> str:
    """Everything the section agents found, with the full source list."""
    urls = {e.id: e.url for e in pager.sources}
    p, out = pager, []
    out.append("## Company Strategy")
    out.append(p.strategy.summary)
    out.append("\n**Initiatives & public statements**")
    out += _claims_md(p.strategy.key_points, urls, "Nothing found in public sources.")
    out.append("\n**Technology & hiring signals**")
    out += _claims_md(p.strategy.tech_signals, urls, "No job-posting or tech-stack signals found.")

    out.append("\n## Competitor Mentions")
    out.append(p.competitor_section.summary)
    for m in p.competitor_section.mentions:
        status = "✅ mentioned" if m.mentioned else "— no mention found"
        out.append(f"\n**{m.competitor}**: {status}")
        out += [f"- {c.text}{_cite_md(c, urls)}" for c in m.findings]

    out.append("\n## Leadership")
    if not p.leadership.leaders:
        out.append("- _No leaders identified in public sources._")
    for leader in p.leadership.leaders:
        out.append(f"- **{leader.name}**, {leader.title}: {leader.relevance}{_cite_md(leader, urls)}")
        if leader.quote:
            out.append(f'  > "{leader.quote}"')
    if p.inputs.target_customer:
        out.append(f"\n**About {p.inputs.target_customer}**")
        out += _claims_md(p.leadership.target_contact_notes, urls, "Nothing public found about this contact.")

    out.append("\n## Product & Financial Strategy")
    out.append("*Publicly traded (SEC filer).*" if p.financials.is_public else "*No public filings found.*")
    out += _claims_md(p.financials.points, urls, "No financial or annual-report insights found.")

    out.append("\n## Sources")
    by_id = {e.id: e for e in p.sources}
    for i in cited_ids(p):
        e = by_id[i]
        date = f", {e.published}" if e.published else ""
        out.append(f"{i}. [{e.title}]({e.url}) ({SOURCE_LABELS[e.source_type]}{date})")
    if any(item.verdict == "partial" for item in claims_of(p)):
        out.append(f"\n{PARTIAL_MARK} = partly supported: some detail could not be confirmed in the cited source.")
    return "\n".join(out)


def to_markdown(pager: OnePager) -> str:
    return f"{brief_markdown(pager)}\n\n---\n\n# Detailed Research\n\n{research_markdown(pager)}"


def to_docx(pager: OnePager, learner: str | None = None) -> bytes:
    """A single page on the Per Scholas template (its header, logo and footer), citations linked to sources."""
    p = pager
    learner = learner if learner is not None else get_settings().learner_name
    by_id = {e.id: e for e in p.sources}

    # Number citations 1..n in reading order so the page footer can list them compactly.
    order: list[int] = []
    for _, items in _brief_sections(p):
        for item in items:
            order += [c for c in item.citations if c in by_id and c not in order]
    number = {c: n for n, c in enumerate(order, 1)}
    # The writer already tightened the brief to fit; fall back to the smallest readable size just in case.
    size = layout.font_size(p.brief) or layout.MIN_READABLE_PT

    doc = t.open_page()
    title = t.paragraph(doc)
    t.run(title, f"Account Brief: {p.company.name}", size=17, bold=True, color=t.BRAND)
    title.paragraph_format.space_after = Pt(1)
    meta = f"Selling {p.inputs.product_name} ({p.intake.category})"
    if p.inputs.target_customer:
        meta += f" · Target: {p.inputs.target_customer}"
    meta += f" · {learner + ' · ' if learner else ''}{p.generated_at:%B %d, %Y}"
    t.paragraph(doc, meta, size=8.5, color=t.MUTED)
    t.paragraph(doc, f"**{p.brief.headline}**", size=size + 0.5)

    for heading, items in _brief_sections(p):
        if not items:
            continue
        t.label(doc, heading, size=size + 1.5)
        for item in items:
            para = t.bullet(doc, size=size)
            if isinstance(item, Leader):
                t.run(para, item.name, size=size, bold=True)
                t.run(para, f", {item.title}: {item.relevance}", size=size)
            else:
                t.run(para, item.text, size=size)
            for c in item.citations:
                if c in number:
                    t.run(para, " ", size=size)
                    t.hyperlink(para, by_id[c].url, f"[{number[c]}]", size=size - 1.5)
            if item.verdict == "partial":
                t.run(para, f" {PARTIAL_MARK}", size=size - 2, color=t.MUTED)

    footer = t.paragraph(doc, size=7)
    footer.paragraph_format.space_before = Pt(6)
    if order:
        t.run(footer, "Sources: ", size=7, bold=True, color=t.MUTED)
        for n, c in enumerate(order, 1):
            t.hyperlink(footer, by_id[c].url, f"[{n}] {domain_of(by_id[c].url)}", size=7)
            t.run(footer, "  ", size=7)
    note = "Each claim was checked against its source by a verifier agent"
    note += f" ({PARTIAL_MARK} = partly supported)" if any(i.verdict == "partial" for i in claims_of(p)) else ""
    t.run(footer, f"· {note}. Full research is in the app and its Markdown/JSON downloads.", size=7, italic=True,
          color=t.MUTED)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
