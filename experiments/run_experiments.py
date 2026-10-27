"""Compare prompt strategies and models on the same evidence, graded by the same verifier.

Arms:
  baseline-single-prompt  one prompt produces the whole brief from all evidence (fast model)
  chained-fast            the multi-agent pipeline, fast model everywhere
  chained-mixed           the multi-agent pipeline, fast research agents + smart writer (the app default)

Usage:  uv run python experiments/run_experiments.py [company_url ...]
Writes experiments/results/<timestamp>/{report.md, <company>-<arm>.json}.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from cap_931 import agents
from cap_931.config import get_settings
from cap_931.llm import LLM
from cap_931.research.gather import gather_evidence
from cap_931.schemas import Claim, Leader, SalesInput

PRODUCT = dict(
    product_name="FlowLake",
    product_category="Cloud data platform",
    value_proposition="FlowLake unifies streaming and batch data in one governed lakehouse, cutting analytics "
                      "infrastructure costs by up to 40% while meeting compliance requirements.",
    competitor_urls=["https://www.snowflake.com", "https://www.databricks.com"],
    target_customer="Chief Data Officer",
)
DEFAULT_COMPANIES = ["https://www.capitalone.com", "https://www.target.com", "https://www.chime.com"]


class BaselineBrief(BaseModel):
    executive_summary: list[Claim] = Field(default_factory=list)
    strategy: list[Claim] = Field(default_factory=list)
    competitor_mentions: list[Claim] = Field(default_factory=list)
    leaders: list[Leader] = Field(default_factory=list)
    financials: list[Claim] = Field(default_factory=list)


def run_baseline(llm: LLM, ctx: agents.Context) -> list:
    system = f"""{agents.GUARDRAILS}

Write a complete one-page account brief for the sales rep: executive summary, company strategy, mentions of our \
competitors, key leaders and product/financial strategy, all from the evidence."""
    brief = llm.json(system, f"{agents.brief(ctx)}\n\n{agents.format_evidence(ctx.evidence)}", BaselineBrief)
    return [brief]


def run_chained(llm: LLM, ctx: agents.Context) -> list:
    strategy = agents.run_strategy(llm, ctx)
    competitors = agents.run_competitors(llm, ctx)
    leadership = agents.run_leadership(llm, ctx)
    financials = agents.run_financials(llm, ctx)
    writer = agents.run_writer(llm, ctx, strategy, competitors, leadership, financials)
    return [writer, strategy, competitors, leadership, financials]


def score(llm: LLM, ctx: agents.Context, sections: list) -> dict:
    items = agents.all_claims(*sections)
    uncited = sum(1 for i in items if not i.citations)
    agents.run_verifier(llm, ctx, items)
    counts = {v: sum(1 for i in items if i.verdict == v) for v in ("supported", "partial", "unsupported")}
    total = len(items) or 1
    return {"claims": len(items), "uncited": uncited, **counts,
            "supported_pct": round(100 * counts["supported"] / total),
            "distinct_sources_cited": len({c for i in items for c in i.citations})}


def main(urls: list[str]) -> None:
    settings = get_settings()
    out_dir = Path(__file__).parent / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True)
    arms = {
        "baseline-single-prompt": (settings.fast_model, run_baseline),
        "chained-fast": (settings.fast_model, run_chained),
        "chained-mixed": (settings.smart_model, run_chained),
    }
    rows = []
    for url in urls:
        inputs = SalesInput(company_url=url, **PRODUCT)
        setup_llm = LLM()
        intake = agents.run_intake(setup_llm, inputs)
        company, competitors, evidence, _ = gather_evidence(inputs, intake, print)
        ctx = agents.Context(inputs, intake, company, competitors, evidence)
        print(f"\n{company.name}: {len(evidence)} sources")
        for arm, (writer_model, run) in arms.items():
            llm = LLM(fast_model=settings.fast_model, smart_model=writer_model)
            start = time.time()
            sections = run(llm, ctx)
            seconds = round(time.time() - start)
            gen_usage = json.loads(json.dumps(llm.usage))  # snapshot before verification
            # Grade every arm with the same verifier model so scores are comparable.
            metrics = score(LLM(smart_model=settings.smart_model), ctx, sections)
            tokens = sum(u["prompt_tokens"] + u["completion_tokens"] for u in gen_usage.values())
            row = {"company": company.name, "arm": arm, "seconds": seconds, "tokens": tokens, **metrics}
            rows.append(row)
            print(row)
            (out_dir / f"{company.domain}-{arm}.json").write_text(
                json.dumps({"metrics": row, "usage": gen_usage, "sections": [s.model_dump() for s in sections]},
                           indent=2))

    header = ["company", "arm", "claims", "supported_pct", "partial", "unsupported", "uncited",
              "distinct_sources_cited", "tokens", "seconds"]
    lines = [f"# Experiment results ({datetime.now():%Y-%m-%d %H:%M})",
             f"Fast model: `{settings.fast_model}` · Smart model: `{settings.smart_model}` · "
             f"Search: {settings.search_provider}", "",
             "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(r[h]) for h in header) + " |" for r in rows]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n")
    print(f"\nReport: {out_dir / 'report.md'}")


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_COMPANIES)
