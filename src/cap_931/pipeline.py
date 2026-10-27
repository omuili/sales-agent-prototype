from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from . import agents
from .llm import LLM
from .research.gather import gather_evidence
from .schemas import Claim, Leader, OnePager, SalesInput

Progress = Callable[[str], None]


class OutOfScopeError(ValueError):
    """The intake agent decided the request is not sales research."""


def prune_unsupported(node) -> int:
    """Remove claims the verifier marked unsupported, recursively. Returns how many were removed."""
    if isinstance(node, list):
        return sum(prune_unsupported(item) for item in node)
    if not isinstance(node, BaseModel):
        return 0
    removed = 0
    for name in type(node).model_fields:
        value = getattr(node, name)
        if isinstance(value, list):
            kept = [v for v in value if not (isinstance(v, (Claim, Leader)) and v.verdict == "unsupported")]
            removed += len(value) - len(kept)
            setattr(node, name, kept)
            value = kept
        removed += prune_unsupported(value)
    return removed


def run_pipeline(inputs: SalesInput, llm: LLM, progress: Progress = lambda _: None, verify: bool = True) -> OnePager:
    progress("Checking the request and normalizing the product details")
    intake = agents.run_intake(llm, inputs)
    if not intake.in_scope:
        raise OutOfScopeError(intake.reason or "This tool only researches prospect accounts for sales reps.")

    company, competitors, evidence, warnings = gather_evidence(inputs, intake, progress)
    ctx = agents.Context(inputs=inputs, intake=intake, company=company, competitors=competitors, evidence=evidence)

    progress(f"Running strategy, competitor, leadership and financial agents on {len(evidence)} sources")
    with ThreadPoolExecutor(max_workers=4) as pool:
        strategy = pool.submit(agents.run_strategy, llm, ctx)
        competitor_section = pool.submit(agents.run_competitors, llm, ctx)
        leadership = pool.submit(agents.run_leadership, llm, ctx)
        financials = pool.submit(agents.run_financials, llm, ctx)
        strategy, competitor_section = strategy.result(), competitor_section.result()
        leadership, financials = leadership.result(), financials.result()

    progress("Writing the one-page brief")
    brief = agents.run_writer(llm, ctx, strategy, competitor_section, leadership, financials)

    sections = (brief, strategy, competitor_section, leadership, financials)
    if verify:
        progress("Verifying every claim against its cited source")
        agents.run_verifier(llm, ctx, agents.all_claims(*sections))
        removed = sum(prune_unsupported(s) for s in sections)
        if removed:
            warnings.append(f"Verifier removed {removed} claim(s) its sources did not support.")

    return OnePager(company=company, inputs=inputs, intake=intake, competitors=competitors, brief=brief,
                    strategy=strategy, competitor_section=competitor_section, leadership=leadership,
                    financials=financials, sources=evidence, warnings=warnings, usage=llm.usage)
