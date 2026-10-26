"""Category policy: the closed taxonomy plus aggregation of a month's spends.

The classifier service (`classifier/knn.py`) answers with a label and a
"confidence" number for free text. It knows nothing about this app's taxonomy,
and its own 0.40 similarity cut-off is meaningless for e5 embeddings — unrelated
Russian phrases already score ~0.7 there. So every decision that matters for
the UI is taken here:

* the taxonomy is closed: anything off-list folds into «Другое»;
* the service reports ``((cos + 1) / 2) * 100``, which we invert back into a
  cosine and test against a floor we control;
* all arithmetic (total / share / avg) happens here, never in the service.

`ai.py` (the legacy YandexGPT path, kept behind `CATEGORIES_BACKEND=llm`)
imports the taxonomy from this module so there is exactly one list.
"""

from datetime import datetime, timedelta

CATEGORIES = [
    "Продукты",
    "Кафе и рестораны",
    "Транспорт и такси",
    "Авто и заправки",
    "Коммуналка",
    "Связь и интернет",
    "Здоровье и аптека",
    "Одежда и обувь",
    "Товары для дома",
    "Электроника",
    "Развлечения и подписки",
    "Спорт и отдых",
    "Образование",
    "Путешествия",
    "Другое",
]

FALLBACK_CATEGORY = "Другое"

_ALLOWED = {name.casefold(): name for name in CATEGORIES}


def is_valid(name):
    """True only for an exact member of the taxonomy."""
    return (name or "").strip().casefold() in _ALLOWED


def normalize_category(name):
    text = (name or "").strip().casefold()
    if not text:
        return FALLBACK_CATEGORY
    if text in _ALLOWED:
        return _ALLOWED[text]
    # Tolerate decorated names like "Продукты / супермаркет" that a dataset
    # label or a hand edit may carry.
    matches = [name for key, name in _ALLOWED.items() if key in text]
    if matches:
        return max(matches, key=len)
    return FALLBACK_CATEGORY


def confidence_to_cosine(confidence):
    """Invert the classifier's ``((cos + 1) / 2) * 100`` back to a cosine."""
    if confidence is None:
        return None
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return None
    return max(-1.0, min(1.0, value / 100.0 * 2.0 - 1.0))


def resolve(category, confidence, floor):
    """Turn a raw classifier answer into a stored decision.

    Returns ``(name, cosine, accepted)``. A label below the floor is replaced by
    «Другое» — the classifier cannot say "I don't know" on its own.
    """
    name = normalize_category(category)
    cosine = confidence_to_cosine(confidence)
    if cosine is not None and cosine < floor:
        return FALLBACK_CATEGORY, cosine, False
    return name, cosine, True


def month_bounds(month=None, now=None):
    """`(start, end)` for a `YYYY-MM` key, or for the current month.

    `spent_at` stores naive local time, so the bounds are naive local too.
    """
    now = now or datetime.now()
    if month:
        try:
            year, mon = (int(part) for part in str(month).split("-", 1))
            start = now.replace(
                year=year, month=mon, day=1, hour=0, minute=0, second=0, microsecond=0
            )
        except (TypeError, ValueError):
            raise ValueError(f"invalid month: {month!r}")
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # +32 days always crosses into the next month, whatever its length.
    end = start + timedelta(days=32)
    end = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, end


def month_key(moment=None):
    return (moment or datetime.now()).strftime("%Y-%m")


def aggregate(spends, assignments, review_cosine=None):
    """Bucket month spends by their stored category.

    `spends` rows arrive newest-first; `assignments` maps ``spend_id`` to
    ``{category, confidence, cosine, source}``. A row without an assignment
    still shows up under «Другое» with ``source == "none"`` so no money ever
    disappears from the report.
    """
    grand_total = round(sum(float(s.get("amount") or 0) for s in spends), 2)
    grouped = {}

    for spend in spends:
        assignment = assignments.get(spend.get("id")) or {}
        name = assignment.get("category") or FALLBACK_CATEGORY
        cosine = assignment.get("cosine")
        source = assignment.get("source") or "none"
        needs_review = source == "knn" and (
            review_cosine is None or cosine is None or cosine < review_cosine
        )
        row = {
            "id": spend.get("id"),
            "date": spend.get("date", ""),
            "amount": round(float(spend.get("amount") or 0), 2),
            "comment": spend.get("comment", ""),
            "confidence": assignment.get("confidence"),
            "cosine": round(cosine, 4) if cosine is not None else None,
            "source": source,
            "needsReview": needs_review,
        }
        grouped.setdefault(name, []).append(row)

    buckets = []
    for name, rows in grouped.items():
        total = round(sum(r["amount"] for r in rows), 2)
        buckets.append(
            {
                "name": name,
                "total": total,
                "count": len(rows),
                "share": _share(total, grand_total),
                "avg": round(total / len(rows), 2) if rows else 0.0,
                "spends": rows,
            }
        )

    # Biggest spend first; the name is a stable tie-breaker so the chart does
    # not reshuffle between identical refreshes.
    buckets.sort(key=lambda b: (-b["total"], b["name"]))
    return buckets


def _share(total, grand_total):
    return round(total / grand_total * 100, 1) if grand_total else 0.0


def build_summary(buckets, grand_total, unclassified=0, low_confidence=0):
    """Deterministic replacement for the old LLM `notice`.

    Free, instant and stable: the same month always renders the same sentence.
    """
    if not buckets or not grand_total:
        return "No spends in this month yet."

    top = buckets[0]
    parts = [
        f"Top category: {top['name']} — {top['share']:.1f}% "
        f"({top['total']:.2f} ₽, {top['count']} spends)"
    ]
    if len(buckets) > 1:
        second = buckets[1]
        parts.append(
            f"then {second['name']} — {second['share']:.1f}% ({second['total']:.2f} ₽)"
        )
    if unclassified:
        parts.append(f"{unclassified} spends could not be classified")
    if low_confidence:
        parts.append(f"{low_confidence} need confirmation")
    return ". ".join(parts) + "."


def build_metrics(spends, buckets, classify_meta, unique_comments):
    unclassified = sum(
        1 for b in buckets for s in b["spends"] if s["source"] == "none"
    )
    low_confidence = sum(1 for b in buckets for s in b["spends"] if s["needsReview"])
    return {
        "rows": len(spends),
        "uniqueComments": unique_comments,
        "classified": classify_meta.get("classified", 0),
        "unclassified": unclassified,
        "lowConfidence": low_confidence,
        "latencyMs": classify_meta.get("latencyMs", 0),
        "degraded": bool(classify_meta.get("degraded")),
    }
