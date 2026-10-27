import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from .config import get_settings


def cached(namespace: str, key: str, compute: Callable[[], Any]) -> Any:
    settings = get_settings()
    digest = hashlib.sha256(f"{namespace}:{key}".encode()).hexdigest()[:32]
    path = settings.cache_dir / namespace / f"{digest}.json"
    if path.exists() and time.time() - path.stat().st_mtime < settings.cache_ttl_hours * 3600:
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    value = compute()
    # Don't cache empty results: they are usually transient failures.
    if value:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    return value
