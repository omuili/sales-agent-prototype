from .schemas import Leader, OnePageBrief

PAGE_SIDE_MARGIN_IN = 0.75
PAGE_BODY_HEIGHT_PT = 8.6 * 72 
MIN_READABLE_PT = 9.0
CALIBRATION = 1.2
HEADROOM = 0.95


def sections(brief: OnePageBrief) -> list[tuple[str, list]]:
    return [("Executive Summary", brief.executive_summary), ("Company Strategy", brief.strategy),
            ("Competitor Landscape", brief.competitor_landscape), ("Key Contacts", brief.key_contacts),
            ("Financial Snapshot", brief.financial_snapshot), ("Talking Points", brief.talking_points)]


def estimated_height(brief: OnePageBrief, size: float) -> float:
    """Approximate rendered height in points at body font `size`."""
    width_pt = (8.5 - 2 * PAGE_SIDE_MARGIN_IN) * 72 - 16 

    def lines(chars: int, font: float) -> int:
        return -(-chars // int(width_pt / (0.5 * font)))  

    sources = len({c for _, items in sections(brief) for item in items for c in item.citations})
    height = 17 * 1.2 + 10 * 1.2 + 6  # title + meta line
    height += lines(len(brief.headline), size + 0.5) * (size + 0.5) * 1.2 + 4
    for _, items in sections(brief):
        if items:
            height += (size + 1.5) * 1.2 + 9
        for item in items:
            text = f"{item.name}, {item.title}: {item.relevance}" if isinstance(item, Leader) else item.text
            height += lines(len(text) + 5 * len(item.citations) + 4, size) * size * 1.17 + 1.5
    height += (lines(sources * 22, 7) + 1) * 7 * 1.2 + 10  
    return height * CALIBRATION


def font_size(brief: OnePageBrief) -> float | None:
    """The largest readable body size at which the brief fits on one page, or None if it doesn't fit."""
    for size in (10, 9.5, 9):
        if estimated_height(brief, size) <= PAGE_BODY_HEIGHT_PT * HEADROOM:
            return size
    return None


def overflow_ratio(brief: OnePageBrief) -> float:
    """How much taller than the page the brief is at the minimum readable size (1.0 = exactly fits)."""
    return estimated_height(brief, MIN_READABLE_PT) / (PAGE_BODY_HEIGHT_PT * HEADROOM)
