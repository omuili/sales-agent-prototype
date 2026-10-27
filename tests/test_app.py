from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import cap_931
from cap_931 import alerts

from .conftest import FakeLLM

APP = str(Path(cap_931.__file__).with_name("app.py"))


class FakeLLMFactory(FakeLLM):
    """Accepts the real LLM constructor arguments."""

    def __init__(self, fast_model=None, smart_model=None, api_key=None):
        super().__init__()

    def list_models(self):
        return ["gpt-5", "gpt-5-mini"]


@pytest.fixture
def app(monkeypatch, fake_gather):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("cap_931.llm.LLM", FakeLLMFactory)
    return AppTest.from_file(APP, default_timeout=30).run()


def test_example_fill_and_generate_brief(app):
    assert not app.exception
    app.button[0].click().run()  # "Fill in an example"
    assert app.text_input(key="company_url").value == "https://www.capitalone.com"
    app.button(key="FormSubmitter:inputs-Generate account brief").click().run()
    assert not app.exception
    assert not app.error, [e.value for e in app.error]
    rendered = "\n".join(m.value for m in app.markdown)
    assert "# Account Brief: Acme Bank" in rendered
    assert "Jane Roe" in rendered
    assert len(app.get("download_button")) == 3


def test_validation_errors_are_shown(app):
    app.text_input(key="company_url").set_value("not a url")
    app.button(key="FormSubmitter:inputs-Generate account brief").click().run()
    errors = [e.value for e in app.error]
    assert any("Product name is required" in e for e in errors)
    assert any("not a valid website" in e for e in errors)


def test_add_watch_from_alerts_tab(app):
    form = app.tabs[1]
    form.text_input[0].set_value("Acme Bank")
    form.text_input[1].set_value("lakehouse, governance")
    form.text_input[2].set_value("rep@example.com")
    app.button(key="FormSubmitter:new_watch-Add watch").click().run()
    assert not app.exception
    assert [w.company for w in alerts.list_watches()] == ["Acme Bank"]
    assert any("Watching Acme Bank" in s.value for s in app.success)
