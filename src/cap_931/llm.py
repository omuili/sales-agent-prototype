import json
import logging
import threading
import time
from typing import TypeVar

import openai
from pydantic import BaseModel, ValidationError

from .config import get_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class LLMError(RuntimeError):
    """A failure the user can act on (bad key, unknown model, repeated invalid output)."""


class LLM:
    def __init__(self, fast_model: str | None = None, smart_model: str | None = None, api_key: str | None = None):
        settings = get_settings()
        self.fast_model = fast_model or settings.fast_model
        self.smart_model = smart_model or settings.smart_model
        key = api_key or settings.openai_api_key
        if not key:
            raise LLMError("OPENAI_API_KEY is not set. Add it to .env or enter it in the sidebar.")
        self.client = openai.OpenAI(api_key=key, timeout=180, max_retries=3)
        self.usage: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()

    def _record(self, model: str, usage, seconds: float) -> None:
        with self._lock:
            entry = self.usage.setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0})
            entry["calls"] += 1
            entry["seconds"] += round(seconds)
            if usage:
                entry["prompt_tokens"] += usage.prompt_tokens
                entry["completion_tokens"] += usage.completion_tokens

    def _create(self, model: str, messages: list[dict]) -> str:
        extra = {"reasoning_effort": "low"} if model.startswith(REASONING_PREFIXES) else {"temperature": 0.2}
        start = time.time()
        try:
            resp = self.client.chat.completions.create(
                model=model, messages=messages, response_format={"type": "json_object"}, **extra
            )
        except openai.AuthenticationError as exc:
            raise LLMError("OpenAI rejected the API key. Check OPENAI_API_KEY.") from exc
        except openai.RateLimitError as exc:
            if "insufficient_quota" in str(exc) or "credit" in str(exc):
                raise LLMError("The OpenAI account has no credits left. Add credits at "
                               "https://platform.openai.com/settings/organization/billing and try again.") from exc
            raise LLMError("OpenAI is rate-limiting requests. Wait a minute and try again.") from exc
        except openai.NotFoundError as exc:
            raise LLMError(f"Model {model!r} is not available to this API key. Pick another in the sidebar.") from exc
        except openai.BadRequestError as exc:
            if "reasoning_effort" in str(exc) or "temperature" in str(exc):
                resp = self.client.chat.completions.create(
                    model=model, messages=messages, response_format={"type": "json_object"}
                )
            else:
                raise
        self._record(model, resp.usage, time.time() - start)
        return resp.choices[0].message.content or ""

    def json(self, system: str, user: str, schema: type[T], smart: bool = False) -> T:
        model = self.smart_model if smart else self.fast_model
        schema_text = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        messages = [
            {"role": "system", "content": f"{system}\n\nRespond with a single JSON object matching this JSON schema:\n{schema_text}"},
            {"role": "user", "content": user},
        ]
        for attempt in range(3):
            content = self._create(model, messages)
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                log.warning("Invalid %s from %s (attempt %d): %s", schema.__name__, model, attempt + 1, exc)
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That JSON did not match the schema: {exc}. Return corrected JSON only."},
                ]
        raise LLMError(f"{model} returned invalid {schema.__name__} three times in a row.")

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list() if m.id.startswith(("gpt-", "o1", "o3", "o4")))
        except openai.OpenAIError:
            return []
