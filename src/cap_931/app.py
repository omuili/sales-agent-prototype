import json
import shutil

import streamlit as st

from cap_931 import alerts
from cap_931.config import get_settings
from cap_931.llm import LLM, LLMError
from cap_931.pipeline import OutOfScopeError, run_pipeline
from cap_931.render import brief_markdown, cited_ids, research_markdown, to_docx, to_markdown
from cap_931.research.deck import SUPPORTED, extract_deck_text
from cap_931.validation import validate_inputs

st.set_page_config(page_title="Sales Account Assistant", page_icon="📇", layout="wide")

EXAMPLE = {
    "product_name": "FlowLake",
    "company_url": "https://www.capitalone.com",
    "product_category": "Cloud data platform / lakehouse",
    "competitors": "https://www.snowflake.com\nhttps://www.databricks.com",
    "value_proposition": "FlowLake unifies streaming and batch data in one governed lakehouse, cutting analytics "
                         "infrastructure costs by up to 40% while meeting financial-services compliance requirements.",
    "target_customer": "Chief Data Officer",
}
FIELDS = ("product_name", "company_url", "product_category", "competitors", "value_proposition", "target_customer")

settings = get_settings()
for name in FIELDS:
    st.session_state.setdefault(name, "")


@st.cache_data(ttl=3600, show_spinner=False)
def available_models(api_key: str) -> list[str]:
    try:
        return LLM(api_key=api_key).list_models()
    except LLMError:
        return []


def load_example() -> None:
    for name, value in EXAMPLE.items():
        st.session_state[name] = value


# ---- Sidebar: settings ----
with st.sidebar:
    st.header("Settings")
    api_key = settings.openai_api_key or st.text_input("OpenAI API key", type="password",
                                                       help="Or set OPENAI_API_KEY in .env")
    if settings.openai_api_key:
        st.success("OpenAI key loaded from environment", icon="🔑")
    models = available_models(api_key) if api_key else []

    def model_picker(label: str, default: str, help_text: str) -> str:
        if models:
            options = models if default in models else [default, *models]
            return st.selectbox(label, options, index=options.index(default), help=help_text)
        return st.text_input(label, value=default, help=help_text)

    fast_model = model_picker("Fast model (research agents)", settings.fast_model,
                              "Runs intake, strategy, competitor, leadership and financial agents.")
    smart_model = model_picker("Smart model (writer & verifier)", settings.smart_model,
                               "Writes the summary and fact-checks every claim.")
    learner = st.text_input("Learner name (document cover)", value=settings.learner_name)
    verify = st.toggle("Verify claims against sources", value=True,
                       help="A verifier agent checks every claim and removes unsupported ones.")
    st.caption(f"Web search: **{settings.search_provider}**"
               + ("" if settings.tavily_api_key else " (set TAVILY_API_KEY for richer results)"))
    if st.button("Clear research cache"):
        shutil.rmtree(settings.cache_dir, ignore_errors=True)
        st.toast("Cache cleared")

st.title("📇 Sales Account Assistant")
st.caption("Research a prospect account from public sources: strategy, competitor mentions, leadership and "
           "financial insights, in a one-page brief with every claim linked to its source.")

research_tab, alerts_tab = st.tabs(["Research an account", "Keyword alerts"])


with research_tab:
    st.button("Fill in an example", on_click=load_example)
    with st.form("inputs"):
        left, right = st.columns(2)
        with left:
            st.text_input("Product name *", key="product_name", placeholder="What product are you selling?")
            st.text_input("Prospect company URL *", key="company_url", placeholder="e.g. capitalone.com")
            st.text_input("Product category", key="product_category",
                          placeholder="e.g. Data Warehousing (leave blank to infer)")
            st.text_input("Target customer", key="target_customer",
                          placeholder="Person you're selling to, e.g. Jane Doe or Chief Data Officer")
        with right:
            st.text_area("Value proposition *", key="value_proposition", height=105,
                         placeholder="One or two sentences on the value your product delivers")
            st.text_area("Competitor URLs", key="competitors", height=105,
                         placeholder="One per line (up to 5), e.g. snowflake.com")
        deck = st.file_uploader("Optional: product overview or deck", type=[s.lstrip(".") for s in SUPPORTED])
        submitted = st.form_submit_button("Generate account brief", type="primary")

    if submitted:
        deck_text, deck_error = "", None
        if deck is not None:
            try:
                deck_text = extract_deck_text(deck.name, deck.getvalue())
                if not deck_text:
                    deck_error = f"No readable text found in {deck.name}."
            except Exception as exc:  # corrupt or password-protected files raise library-specific errors
                deck_error = f"Could not read {deck.name}: {exc}"
        inputs, errors = validate_inputs(
            product_name=st.session_state.product_name, company_url=st.session_state.company_url,
            value_proposition=st.session_state.value_proposition, product_category=st.session_state.product_category,
            competitors=st.session_state.competitors, target_customer=st.session_state.target_customer,
            deck_text=deck_text,
        )
        if deck_error:
            errors.append(deck_error)
        if not api_key:
            errors.append("Add an OpenAI API key in the sidebar or .env.")
        if errors:
            for error in errors:
                st.error(error)
        else:
            with st.status("Researching the account…", expanded=True) as status:
                try:
                    llm = LLM(fast_model=fast_model, smart_model=smart_model, api_key=api_key)
                    pager = run_pipeline(inputs, llm, progress=st.write, verify=verify)
                    st.session_state.pager = pager
                    status.update(label="Account brief ready", state="complete", expanded=False)
                except OutOfScopeError as exc:
                    status.update(label="Request declined", state="error")
                    st.warning(f"This assistant only researches prospect accounts. {exc}")
                except LLMError as exc:
                    status.update(label="LLM error", state="error")
                    st.error(str(exc))

    pager = st.session_state.get("pager")
    if pager:
        for warning in pager.warnings:
            st.info(warning)
        slug = pager.company.name.lower().replace(" ", "-")
        col1, col2, col3 = st.columns(3)
        col1.download_button("⬇ One-page brief (.docx)", to_docx(pager, learner), f"{slug}-account-brief.docx",
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        col2.download_button("⬇ Brief + full research (.md)", to_markdown(pager), f"{slug}-account-brief.md",
                             "text/markdown")
        col3.download_button("⬇ Raw research (.json)", pager.model_dump_json(indent=2), f"{slug}-research.json",
                             "application/json")
        # Escape $ so amounts like "$51.8 billion ... $168.6 billion" aren't rendered as LaTeX math.
        with st.container(border=True):
            st.markdown(brief_markdown(pager).replace("$", r"\$"))
        with st.expander("Detailed research: everything the agents found"):
            st.markdown(research_markdown(pager).replace("$", r"\$"))

        with st.expander(f"All {len(pager.sources)} sources collected ({len(cited_ids(pager))} cited)"):
            st.dataframe(
                [{"id": e.id, "type": e.source_type, "title": e.title, "url": e.url, "published": e.published,
                  "chars": len(e.text)} for e in pager.sources],
                column_config={"url": st.column_config.LinkColumn()}, hide_index=True, width="stretch",
            )
        with st.expander("Run details: models, tokens, intake"):
            st.json({"intake": pager.intake.model_dump(), "usage": pager.usage})

with alerts_tab:
    st.write("Get an email when new press releases, news or job postings about a company match your keywords. "
             "A scheduled job (`uv run cap-931-alerts`) checks every watch; you can also check one now.")
    last = st.session_state.get("pager")
    with st.form("new_watch", clear_on_submit=True):
        company = st.text_input("Company name", value=last.company.name if last else "")
        keywords = st.text_input("Keywords (comma-separated)",
                                 value=", ".join(last.intake.search_keywords[:3]) if last else "")
        email = st.text_input("Send alerts to (email)")
        if st.form_submit_button("Add watch"):
            kw = [k.strip() for k in keywords.split(",") if k.strip()]
            if not company.strip() or not kw or "@" not in email:
                st.error("Company, at least one keyword and a valid email are required.")
            else:
                alerts.add_watch(company, kw, email)
                st.success(f"Watching {company} for: {', '.join(kw)}")
    if not (settings.smtp_user and settings.smtp_password):
        st.caption("SMTP_USER / SMTP_PASSWORD are not set, so new items are shown here and not emailed.")

    for watch in alerts.list_watches():
        with st.container(border=True):
            st.markdown(f"**{watch.company}**: {', '.join(watch.keywords)} → {watch.email}")
            check, remove = st.columns([1, 1])
            if check.button("Check now", key=f"check-{watch.id}"):
                with st.spinner("Searching…"):
                    items = alerts.check_watch(watch)
                if not items:
                    st.write("No new items since the last check.")
                else:
                    digest = alerts.format_digest(watch, items)
                    try:
                        emailed = alerts.send_email(watch.email, f"[Sales alert] {watch.company}", digest)
                    except Exception as exc:  # SMTP auth/connection errors
                        emailed = False
                        st.error(f"Email failed: {exc}")
                    st.success(f"{len(items)} new item(s)" + (" emailed." if emailed else "."))
                    for item in items:
                        st.markdown(f"- [{item['title']}]({item['url']}) _{item['keyword']}_")
            if remove.button("Delete", key=f"delete-{watch.id}"):
                alerts.delete_watch(watch.id)
                st.rerun()
