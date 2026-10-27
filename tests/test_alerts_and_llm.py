from types import SimpleNamespace

import pytest

from cap_931 import alerts
from cap_931.llm import LLM, LLMError
from cap_931.schemas import Intake


def fake_search(results):
    def search(query, max_results=5, news=False, use_cache=True):
        return results
    return search


def test_alert_reports_only_new_matching_items(monkeypatch):
    hit = {"title": "Acme Bank expands lakehouse team", "url": "https://n.com/1", "snippet": "Acme Bank lakehouse",
           "content": "", "published": "2026-10-01"}
    off_topic = {"title": "Other Co news", "url": "https://n.com/2", "snippet": "unrelated", "content": "",
                 "published": ""}
    monkeypatch.setattr(alerts, "search", fake_search([hit, off_topic]))

    watch_id = alerts.add_watch("Acme Bank", ["lakehouse"], "rep@example.com")
    watch = alerts.list_watches()[0]
    assert watch.id == watch_id and watch.keywords == ["lakehouse"]

    first = alerts.check_watch(watch)
    assert [i["url"] for i in first] == ["https://n.com/1"]
    assert alerts.check_watch(watch) == []  # already seen
    assert "Acme Bank expands lakehouse team" in alerts.format_digest(watch, first)
    assert alerts.send_email("rep@example.com", "s", "b") is False  # SMTP not configured

    alerts.delete_watch(watch_id)
    assert alerts.list_watches() == []


def _response(content):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


def test_llm_retries_invalid_json_then_validates():
    llm = LLM(api_key="test-key", fast_model="gpt-test")
    replies = iter(['{"in_scope": true}', '{"in_scope": true, "category": "CDP", "search_keywords": ["a"], '
                                          '"product_summary": "x"}'])
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        return _response(next(replies))

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = llm.json("system", "user", Intake)
    assert result.category == "CDP"
    assert len(sent) == 2 and "did not match the schema" in sent[1]["messages"][-1]["content"]
    assert sent[0]["temperature"] == 0.2  # non-reasoning model
    assert llm.usage["gpt-test"] == {"calls": 2, "prompt_tokens": 20, "completion_tokens": 10, "seconds": 0}


def test_reasoning_models_get_reasoning_effort():
    llm = LLM(api_key="test-key", fast_model="gpt-5-mini")
    sent = []
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: sent.append(kw) or _response('{"in_scope": false, "category": "c", '
                                                         '"search_keywords": [], "product_summary": ""}'))))
    llm.json("s", "u", Intake)
    assert sent[0]["reasoning_effort"] == "low" and "temperature" not in sent[0]


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        LLM()


def test_exhausted_credits_is_a_clear_error():
    import httpx
    import openai

    llm = LLM(api_key="test-key", fast_model="gpt-test")
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))

    def create(**kwargs):
        raise openai.RateLimitError("You have no credits remaining", response=response,
                                    body={"code": "insufficient_quota"})

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(LLMError, match="no credits left"):
        llm.json("s", "u", Intake)
