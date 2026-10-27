import re

import pytest

from cap_931 import agents
from cap_931.schemas import (
    Claim, CompanyProfile, CompetitorMention, CompetitorSection, Evidence, FinancialSection, Intake, Leader,
    LeadershipSection, OnePageBrief, SalesInput, StrategySection,
)

EVIDENCE = [
    Evidence(id=1, title="Acme Bank tech blog", url="https://acmebank.com/tech", source_type="company_site",
             text="Acme Bank is moving all analytics to a cloud lakehouse. We run Snowflake for reporting."),
    Evidence(id=2, title="Acme names new CDO", url="https://news.example.com/acme-cdo", source_type="news",
             published="2026-05-01",
             text='Acme Bank appointed Jane Roe as Chief Data Officer. "Data is our product," Roe said.'),
    Evidence(id=3, title="ACME BANK CORP Form 10-K", url="https://sec.gov/acme-10k", source_type="filing",
             text="BUSINESS OVERVIEW: Acme Bank serves 4 million customers and invests $1B annually in technology."),
]


class FakeLLM:
    """Stands in for LLM: returns canned, schema-valid outputs and records which agents ran."""

    def __init__(self, in_scope=True, unsupported_text="UNSUPPORTED", brief_words=0):
        self.in_scope = in_scope
        self.brief_words = brief_words  # pad the brief to test the writer's length control
        self.unsupported_text = unsupported_text
        self.calls: list[str] = []
        self.usage = {"fake": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0}}

    def json(self, system, user, schema, smart=False):
        self.calls.append(schema.__name__)
        self.usage["fake"]["calls"] += 1
        if schema is Intake:
            return Intake(in_scope=self.in_scope, reason="" if self.in_scope else "Not a sales request",
                          category="Cloud Data Platform", search_keywords=["lakehouse", "analytics"],
                          product_summary="A lakehouse.")
        if schema is StrategySection:
            return StrategySection(summary="Acme is moving analytics to the cloud.",
                                   key_points=[Claim(text="Acme is moving analytics to a lakehouse", citations=[1]),
                                               Claim(text="UNSUPPORTED claim", citations=[2]),
                                               Claim(text="Claim citing a missing source", citations=[99])],
                                   tech_signals=[])
        if schema is CompetitorSection:
            return CompetitorSection(summary="Snowflake is in use.", mentions=[
                CompetitorMention(competitor="Snowflake", mentioned=True,
                                  findings=[Claim(text="Acme runs Snowflake for reporting", citations=[1])])])
        if schema is LeadershipSection:
            return LeadershipSection(leaders=[Leader(name="Jane Roe", title="Chief Data Officer",
                                                     relevance="Owns data strategy", quote="Data is our product",
                                                     citations=[2])])
        if schema is FinancialSection:
            return FinancialSection(is_public=True, points=[Claim(text="Invests $1B a year in tech", citations=[3])])
        if schema is OnePageBrief:
            self.brief_calls = getattr(self, "brief_calls", 0) + 1
            filler = Claim(text=" ".join(["word"] * self.brief_words), citations=[1]) if self.brief_words else None
            return OnePageBrief(
                headline="Acme is consolidating its data platforms.",
                executive_summary=[Claim(text="Acme is modernizing data", citations=[1, 2])] + ([filler] * bool(filler)),
                competitor_landscape=[Claim(text="Acme runs Snowflake for reporting", citations=[1])],
                key_contacts=[Leader(name="Jane Roe", title="Chief Data Officer", relevance="Owns data strategy",
                                     citations=[2])],
                financial_snapshot=[Claim(text="Invests $1B a year in tech", citations=[3])],
                talking_points=[Claim(text="Ask Jane Roe about the lakehouse", citations=[2])])
        if schema is agents.VerifierOutput:
            claims = re.findall(r'<claim id="(c\d+)"[^>]*>(.*?)</claim>', user)
            return agents.VerifierOutput(results=[
                agents.ClaimCheck(claim_id=cid, verdict="unsupported" if self.unsupported_text in text else "supported")
                for cid, text in claims])
        raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture
def sales_input():
    return SalesInput(product_name="FlowLake", company_url="https://acmebank.com", product_category="",
                      competitor_urls=["https://www.snowflake.com"], value_proposition="A governed lakehouse.",
                      target_customer="Jane Roe")


@pytest.fixture
def fake_gather(monkeypatch):
    """Replace the network-bound research layer with fixed evidence."""
    def gather(inputs, intake, progress=lambda _: None):
        company = CompanyProfile(name="Acme Bank", domain="acmebank.com", url="https://acmebank.com")
        competitors = [CompanyProfile(name="Snowflake", domain="snowflake.com", url="https://www.snowflake.com")]
        return company, competitors, [e.model_copy() for e in EVIDENCE], []

    monkeypatch.setattr("cap_931.pipeline.gather_evidence", gather)
    return gather


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ALERTS_DB", str(tmp_path / "alerts.db"))
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
