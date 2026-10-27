from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["supported", "partial", "unsupported", "unverified"]
SourceType = Literal["company_site", "news", "jobs", "leadership", "filing", "competitor_site", "web"]


class SalesInput(BaseModel):
    product_name: str
    company_url: str
    product_category: str = ""
    competitor_urls: list[str] = Field(default_factory=list)
    value_proposition: str
    target_customer: str = ""
    deck_text: str = ""


class Evidence(BaseModel):
    id: int
    title: str
    url: str
    source_type: SourceType
    text: str
    published: str = ""


class CompanyProfile(BaseModel):
    name: str
    domain: str
    url: str
    description: str = ""




class Intake(BaseModel):
    in_scope: bool = Field(description="False if the inputs are not a genuine sales-research request")
    reason: str = Field(default="", description="Why the request is out of scope, if it is")
    category: str = Field(description="Short product category label, e.g. 'Cloud Data Platform'")
    search_keywords: list[str] = Field(description="3-6 keywords describing the problem space the product addresses")
    product_summary: str = Field(description="Two sentences on what the product does and for whom")




class Claim(BaseModel):
    text: str
    citations: list[int] = Field(default_factory=list)
    verdict: Verdict = "unverified"


class StrategySection(BaseModel):
    summary: str = Field(description="2-3 sentence overview of the company's direction in the product's area")
    key_points: list[Claim] = Field(default_factory=list, description="Initiatives, public statements, press releases")
    tech_signals: list[Claim] = Field(default_factory=list, description="Hiring and technology-stack signals, e.g. from job postings")


class CompetitorMention(BaseModel):
    competitor: str
    mentioned: bool
    findings: list[Claim] = Field(default_factory=list)


class CompetitorSection(BaseModel):
    summary: str
    mentions: list[CompetitorMention] = Field(default_factory=list)


class Leader(BaseModel):
    name: str
    title: str
    relevance: str = Field(description="Why this person matters for this sale")
    quote: str = Field(default="", description="A recent public statement, verbatim from the evidence, if any")
    citations: list[int] = Field(default_factory=list)
    verdict: Verdict = "unverified"


class LeadershipSection(BaseModel):
    leaders: list[Leader] = Field(default_factory=list)
    target_contact_notes: list[Claim] = Field(
        default_factory=list, description="What the evidence says about the named target customer, if anything"
    )


class FinancialSection(BaseModel):
    is_public: bool
    points: list[Claim] = Field(default_factory=list, description="Insights from 10-Ks, annual reports, earnings")


class OnePageBrief(BaseModel):
    """The printed one-pager: the writer agent decides what is worth including and how much."""
    headline: str = Field(description="One sentence: the most important thing to know about this account for this sale")
    executive_summary: list[Claim] = Field(default_factory=list)
    strategy: list[Claim] = Field(default_factory=list, description="Initiatives and tech signals that matter for this sale")
    competitor_landscape: list[Claim] = Field(default_factory=list)
    key_contacts: list[Leader] = Field(default_factory=list, description="Who to talk to; relevance in under 12 words")
    financial_snapshot: list[Claim] = Field(default_factory=list)
    talking_points: list[Claim] = Field(default_factory=list)

    def word_count(self) -> int:
        texts = [self.headline] + [c.text for c in self.executive_summary + self.strategy + self.competitor_landscape
                                   + self.financial_snapshot + self.talking_points]
        texts += [f"{c.name} {c.title} {c.relevance}" for c in self.key_contacts]
        return sum(len(t.split()) for t in texts)


class OnePager(BaseModel):
    company: CompanyProfile
    inputs: SalesInput
    intake: Intake
    competitors: list[CompanyProfile] = Field(default_factory=list)
    brief: OnePageBrief
    strategy: StrategySection
    competitor_section: CompetitorSection
    leadership: LeadershipSection
    financials: FinancialSection
    sources: list[Evidence]
    warnings: list[str] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=datetime.now)
