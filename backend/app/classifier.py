"""HTTP client for the classifier service (`classifier/knn.py`, POST /predict).

The service is deliberately left untouched: it has no batch endpoint and no
health check, it returns `{"category": str, "confidence": float}` and 503 until
its vector base is built. Everything we need on top of that lives here:

* one request per **unique** comment (the same comment appears many times in a
  month, so deduplication cuts the traffic by a lot);
* a bounded thread pool instead of a request per row in series;
* a single probe request before fanning out, so a dead service costs one
  connection attempt instead of hundreds;
* `None` instead of an exception — classification is never allowed to break
  saving a spend or rendering the report.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import current_app

# Retry only on transport errors and the "vectors are still warming up" 503.
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)
_BACKOFF_BASE = 0.2


def normalize_comment(comment):
    """Mirror of the service's own `strip().lower()`, used as the cache key."""
    return " ".join((comment or "").strip().casefold().split())


def _settings():
    config = current_app.config
    return {
        "url": (config.get("CLASSIFIER_URL") or "").rstrip("/"),
        "timeout": float(config.get("CLASSIFIER_TIMEOUT", 3)),
        "retries": int(config.get("CLASSIFIER_MAX_RETRIES", 1)),
        "concurrency": max(1, int(config.get("CLASSIFIER_CONCURRENCY", 8))),
        "floor": float(config.get("CLASSIFIER_COSINE_FLOOR", 0.80)),
        "review": float(config.get("CLASSIFIER_REVIEW_COSINE", 0.86)),
    }


def is_enabled():
    return bool(_settings()["url"])


def predict_one(word, retries=None):
    """One request. Returns `{"category", "confidence"}` or `None`."""
    settings = _settings()
    if not settings["url"]:
        return None
    attempts = (settings["retries"] if retries is None else retries) + 1

    for attempt in range(attempts):
        try:
            resp = requests.post(
                f"{settings['url']}/predict",
                json={"word": word},
                timeout=settings["timeout"],
            )
        except requests.RequestException:
            if attempt + 1 == attempts:
                return None
            time.sleep(_BACKOFF_BASE * 2**attempt)
            continue

        if resp.status_code in _RETRYABLE_STATUS:
            if attempt + 1 == attempts:
                return None
            time.sleep(_BACKOFF_BASE * 2**attempt)
            continue

        if resp.status_code >= 400:
            return None

        try:
            data = resp.json()
        except ValueError:
            return None
        if not isinstance(data, dict) or not data.get("category"):
            return None
        return {
            "category": data.get("category"),
            "confidence": data.get("confidence"),
        }

    return None


def predict_many(comments):
    """Classify a list of raw comments.

    Returns ``(results, meta)`` where `results` is keyed by
    :func:`normalize_comment` and `meta` carries counts plus a `degraded` flag
    the UI uses to say "the classifier is down, showing saved categories".
    """
    words = {}
    for comment in comments:
        key = normalize_comment(comment)
        if key:
            words.setdefault(key, comment)

    meta = {
        "unique": len(words),
        "failed": 0,
        "latencyMs": 0,
        "degraded": False,
    }
    if not words:
        return {}, meta

    settings = _settings()
    if not settings["url"]:
        meta["degraded"] = True
        meta["failed"] = len(words)
        return {}, meta

    started = time.perf_counter()

    # Probe once: a cold or dead service should cost one attempt, not one per
    # row (a heavy month has hundreds of unique comments).
    keys = list(words)
    first = predict_one(words[keys[0]])
    if first is None:
        meta.update(
            failed=len(words),
            degraded=True,
            latencyMs=int((time.perf_counter() - started) * 1000),
        )
        return {}, meta

    results = {keys[0]: first}
    rest = keys[1:]
    if rest:
        # The classifier answers one word per request; the pool keeps the total
        # wall time at ~ceil(n / concurrency) round trips.
        workers = min(settings["concurrency"], len(rest))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, answer in zip(rest, pool.map(predict_one, (words[k] for k in rest))):
                if answer is None:
                    meta["failed"] += 1
                else:
                    results[key] = answer

    meta["latencyMs"] = int((time.perf_counter() - started) * 1000)
    # Partial failures are not "degraded": most categories are still real. Only
    # losing everything means the UI should warn the user.
    if not results:
        meta["degraded"] = True
    return results, meta
