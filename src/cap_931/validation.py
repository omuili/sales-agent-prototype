import re

from .research.fetch import domain_of, normalize_url
from .schemas import SalesInput

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
MAX_COMPETITORS = 5


def clean_url(raw: str) -> str | None:
    """Normalize a URL and return it, or None if it isn't a plausible public website."""
    raw = raw.strip()
    if not raw or " " in raw:
        return None
    url = normalize_url(raw)
    domain = domain_of(url).split(":")[0]
    if not DOMAIN_RE.match(domain) or domain in ("localhost",):
        return None
    return url


def split_list(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\n,;]+", raw) if part.strip()]


def validate_inputs(product_name: str, company_url: str, value_proposition: str, product_category: str = "",
                    competitors: str = "", target_customer: str = "", deck_text: str = ""
                    ) -> tuple[SalesInput | None, list[str]]:
    errors = []
    product_name, value_proposition = product_name.strip(), value_proposition.strip()
    product_category, target_customer = product_category.strip(), target_customer.strip()

    if not product_name:
        errors.append("Product name is required.")
    elif len(product_name) > 100:
        errors.append("Product name must be 100 characters or fewer.")

    url = clean_url(company_url)
    if not company_url.strip():
        errors.append("Company URL is required.")
    elif not url:
        errors.append(f"Company URL {company_url.strip()!r} is not a valid website address (e.g. acme.com).")

    if len(value_proposition) < 10:
        errors.append("Value proposition is required (at least one full sentence).")
    elif len(value_proposition) > 600:
        errors.append("Value proposition must be 600 characters or fewer.")

    if len(product_category) > 200:
        errors.append("Product category must be 200 characters or fewer.")
    if len(target_customer) > 100:
        errors.append("Target customer must be 100 characters or fewer.")

    competitor_urls: list[str] = []
    for raw in split_list(competitors):
        cleaned = clean_url(raw)
        if not cleaned:
            errors.append(f"Competitor {raw!r} is not a valid website address.")
        elif url and domain_of(cleaned) == domain_of(url):
            errors.append(f"Competitor {raw!r} is the same site as the prospect.")
        elif domain_of(cleaned) not in {domain_of(c) for c in competitor_urls}:
            competitor_urls.append(cleaned)
    if len(competitor_urls) > MAX_COMPETITORS:
        errors.append(f"Enter at most {MAX_COMPETITORS} competitors.")

    if errors:
        return None, errors
    return SalesInput(product_name=product_name, company_url=url, product_category=product_category,
                      competitor_urls=competitor_urls, value_proposition=value_proposition,
                      target_customer=target_customer, deck_text=deck_text), []
