# Technical Documentation: Sales Account Assistant

## 1. What the system does

A sales rep enters what they sell and who they are selling to. The system collects public evidence about the
prospect, runs a chain of specialized LLM agents over that evidence, and returns a one-page brief. Every statement
in the brief cites the source it came from, and a verifier agent removes statements the sources don't support.

## 2. Architecture

```
┌ Streamlit UI (app.py) ───────────────────────────────────────────────────────────────┐
│ product name · company URL · category · competitor URLs · value prop · target · deck │
└────────────────────────────────────────┬─────────────────────────────────────────────┘
                                         ▼
 validation.py      URL normalization, required fields, length limits, ≤5 competitors,
                    competitor ≠ prospect, deck parsing (PDF/PPTX/DOCX/TXT)
                                         ▼
 Intake agent       scope guardrail (refuses non-sales requests) + infers category and
                    search keywords from the product description and deck
                                         ▼
 research/gather    1. prospect homepage + about/leadership/news/investor/careers pages
                    2. 9+ targeted searches: news, press, executive statements, leadership,
                       job postings, annual report, prospect×competitor pairs
                    3. filters out results that never mention the prospect
                    4. full-text extraction of top results (trafilatura)
                    5. SEC EDGAR: latest 10-K → Business, MD&A, keyword-relevant passages
                    → numbered evidence pool [1..n], each trimmed around prospect mentions
                                         ▼
 Section agents     Strategy │ Competitor Mentions │ Leadership │ Product & Financial
 (parallel)         each sees only evidence types relevant to its job
                                         ▼
 Writer agent       the one-page brief: chooses what matters for this sale and condenses
                    it; redrafts until it fits one printed page at a readable size
                                         ▼
 Verifier agent     checks every claim against its cited evidence:
                    supported / partial (flagged ⚠) / unsupported (removed)
                                         ▼
 render.py          one-page DOCX on the Per Scholas template (font sized to fit, citations
                    linked to sources) · brief + full research in app / Markdown · JSON
```

## 3. Key design decisions

| Decision | Why |
|---|---|
| **Gather the evidence first; the LLM only writes from it** | LLMs make up executives, quotes and partnerships when asked about companies from memory. Every agent must cite evidence ids. The prompt treats a missing source as "not found", never as something to guess. |
| **One agent per section, not one big prompt** | Narrow prompts are easier to tune and test, and each agent gets a smaller, more relevant context. Sections run in parallel, so the chain adds little time. |
| **The writer fits the brief to one page** | The section agents gather everything; the writer decides what matters for *this* sale and how much space each section gets (no fixed counts). `layout.py` estimates the printed height (calibrated against Word); if the draft would overflow at 9pt, the writer is asked to cut it by the measured amount. The renderer then picks the largest font (10 → 9pt) that fits. |
| **Verifier agent** | Even grounded agents sometimes overstate. A second pass with the stronger model grades each claim against only its cited text. Unsupported claims are removed and partial ones are flagged. Citations to evidence ids that don't exist are dropped in code before the verifier runs. |
| **Competitor mentions found in code first** | A regex finds which evidence mentions each competitor before any LLM call. If none does, the section is filled without an LLM call, which removes the most common hallucination (made-up vendor relationships). |
| **Search filter on the prospect's name** | Search engines return loosely related results. Results whose title, snippet or text never mention the prospect are dropped. |
| **SEC EDGAR for 10-Ks** | Free, authoritative and structured. The parser skips the table of contents to find the real *Item 1. Business* and *Item 7. MD&A* sections, then scores paragraphs against the product's keywords. |
| **Plain Python + Pydantic, no agent framework** | Fewer dependencies and easier to explain and debug. Every agent output is a Pydantic model, and invalid JSON is retried with the validation error fed back to the model. |
| **Disk cache (24h) for search, pages and filings** | Repeat runs are faster and cheaper, and demos still work if a site starts rate-limiting. The alert job bypasses it. |
| **DuckDuckGo fallback** | The app works without a paid search key. Tavily is used when its key is set. |

## 4. Model selection

| Role | Default | Why |
|---|---|---|
| Intake, Strategy, Competitor, Leadership, Financial agents | `gpt-5-mini` (fast) | Extraction from supplied text is a well-bounded task. The small model handles it accurately at a fraction of the cost and latency, and these agents use most of the tokens (they read the evidence). |
| Writer, Verifier | `gpt-5` (smart) | Synthesis and fact-checking need stronger reasoning, and these calls use few tokens (section outputs, or only the cited excerpts), so the higher price adds little to the total cost. |

- Reasoning-model calls use `reasoning_effort="low"` to keep latency down. Non-reasoning models get `temperature=0.2`.
- Both models can be changed in `.env` or in the sidebar. The sidebar lists every model the API key can access.
- Token usage per model is shown under **Run details** after each run, so cost can be checked.

## 5. Guardrails (staying on task)

- **Shared system prompt** (`agents.GUARDRAILS`). It:
  - limits every agent to account research;
  - requires citations;
  - forbids outside knowledge about the prospect;
  - treats everything inside `<evidence>`, `<inputs>` and `<deck>` tags as untrusted data whose instructions are ignored. This defends against prompt injection from scraped pages.
- **Intake scope check:** requests unrelated to sales research (e.g. "write me a poem" in the value proposition) are refused before any research runs.
- **Input validation:** bad URLs, missing fields, oversized fields and too many competitors are rejected with a specific message per field.

## 6. Inputs handling

| Field | Handling |
|---|---|
| Product name | required, ≤100 chars |
| Company URL | required. `https://` is added if missing, and the domain is validated. The company name comes from the site's schema.org data, then `og:site_name`, then the page title |
| Product category | optional, a word or a sentence. The intake agent normalizes it, or infers it from the description and deck |
| Competitor URLs | up to 5, separated by newlines or commas, de-duplicated, must differ from the prospect. Names are shortened to their brand ("Snowflake AI Data Cloud" → "Snowflake") for searching |
| Value proposition | required, 10–600 chars |
| Target customer | optional person or role. It's searched alongside the company, and findings appear under "About ⟨name⟩" |
| Deck upload | PDF, PPTX, DOCX, TXT or MD. Text is extracted and used by the intake agent |

## 7. Code structure

See the layout in the [README](../README.md). The data flow is entirely through `schemas.py` models:
`SalesInput → Intake → Evidence[] → section models → WriterOutput → OnePager`.

## 8. Testing

`uv run pytest` runs 31 tests offline. A fake LLM returns schema-valid outputs, and the research layer is
stubbed. They cover:
- the full pipeline, including verifier pruning and the out-of-scope refusal;
- competitor detection;
- the evidence budget;
- Markdown and DOCX rendering with hyperlinks;
- input validation;
- the HTML helpers and 10-K section parsing;
- deck extraction;
- alerts deduplication;
- LLM retry on invalid JSON;
- the Per Scholas template documents (cover filled, placeholders removed, report sections and contents);
- the Streamlit UI end to end via `AppTest` (fill in the example → generate → downloads; validation errors; adding an alert).

## 9. Optional enhancements implemented

1. **Verifier agent (output improvement).** Grades every claim, removes unsupported ones, and flags partial ones.
2. **Keyword alert system.**
   - In the app's "Keyword alerts" tab, a rep watches a company + keywords + an email address.
   - `uv run cap-931-alerts` (cron or another scheduler) searches news and job postings for each watch.
   - It stores already-seen URLs in SQLite and emails only new items over SMTP.
   - "Check now" runs it on demand.
3. **Deck upload** to enrich the product context.

### Using other model types (proposal)
- **Meeting deck generation:**
  - The one-pager JSON is already structured, so a deck generator can map each section to a slide using `python-pptx`.
  - An image model could generate the title visuals.
- **Rep-supplied documents** (RFPs, call notes, CRM exports):
  - Embed them with an embedding model.
  - Retrieve the relevant chunks into the evidence pool with their own source type, so they are cited like any other evidence.
- **Call preparation:**
  - A speech-to-text model transcribes previous calls.
  - The leadership agent can then add what each executive said in person.

## 10. Challenges and solutions

| Challenge | Solution |
|---|---|
| Search engines returned unrelated news (e.g. general market stories) | Results that don't mention the prospect are dropped. |
| DuckDuckGo returned "no results" when many queries ran in parallel | At most 2 concurrent queries, one retry, then a looser query without quotes or `OR`. |
| Competitor names scraped from sites were marketing names ("Snowflake AI Data Cloud") and matched nothing | The name is shortened to the brand word from the domain. |
| The 10-K parser first grabbed a cross-reference ("see Item 1. Business—Supervision…") | Take the first heading followed by more than 3,000 characters before the next item. This skips both the table of contents and later cross-references. |
| 10-K text had no paragraph breaks, so relevance scoring failed | Insert line breaks at block elements before extracting text. |
| Long articles mention the prospect far below the fold | `focus_text` keeps paragraphs that mention the prospect or competitors first, within the character budget. |
| Models sometimes return JSON that doesn't match the schema | Pydantic validation, with the error sent back to the model, up to 3 attempts. |
| The "one-pager" came out 2–8 pages long | Make the writer agent choose the content, and add a layout estimate that measures the printed height. The writer redrafts until the brief fits. |
| Some lines switched font partway in Word | The model writes non-breaking hyphens (U+2011), which Word drew in a fallback font. Normalize those characters and set the font for every script slot. |
| The Per Scholas template numbered pages from 0, and its cover pushed content around | For the one-pager, start from the template's header and footer only, remove the page-numbering offset, and start content with "page break before" rather than relying on spacing. |
| A leader's "quote" was sometimes text *about* them | The leadership prompt now accepts only words the person said themselves. |
| The OpenAI credits ran out during testing and the app showed a raw error | Quota and rate-limit errors become plain messages with the billing link. |
