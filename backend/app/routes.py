import re
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from . import ai, categories, classifier, db
from .auth import require_auth

bp = Blueprint("spends", __name__, url_prefix="/api/spends")
ai_bp = Blueprint("ai", __name__, url_prefix="/api/ai")
stats_bp = Blueprint("stats", __name__, url_prefix="/api/stats")

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _parse_payload(payload):
    amount = payload.get("amount")
    if amount is None:
        return None, "amount is required"

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None, "amount must be a number"

    if amount < 0:
        return None, "amount must be non-negative"

    comment = payload.get("comment", "")
    if comment is None:
        comment = ""

    spent_at = payload.get("date")
    if spent_at:
        try:
            spent_at = datetime.fromisoformat(str(spent_at).replace("Z", "+00:00"))
        except ValueError:
            return None, "date must be in ISO format"
    return {"amount": amount, "comment": str(comment), "spent_at": spent_at}, None


def _serialize(row):
    spent_at = row["spent_at"]
    if isinstance(spent_at, datetime):
        spent_at = spent_at.isoformat()
    return {
        "id": row["id"],
        "amount": float(row["amount"]),
        "comment": row["comment"],
        "date": spent_at,
    }


def _iso_date(spent_at):
    if isinstance(spent_at, datetime):
        return spent_at.isoformat()
    return str(spent_at)


# --- classification --------------------------------------------------------
# Categories are persisted next to the spend (own table) instead of being
# recomputed on every click: the report then becomes a cheap GROUP BY, and the
# stored label survives a classifier outage.


def _classify_row(spend_id, comment):
    """Classify one freshly written spend. Never raises, never blocks a save."""
    if not classifier.is_enabled():
        return None
    try:
        if not classifier.normalize_comment(comment):
            # The service rejects empty input (min_length=1); an empty comment
            # has no signal anyway, so store «Другое» once and stop retrying.
            db.upsert_category(
                spend_id, categories.FALLBACK_CATEGORY, source="fallback"
            )
            return categories.FALLBACK_CATEGORY

        # One attempt only on the write path: a slow classifier must not delay
        # saving a spend. The report/backfill retries lazily.
        answer = classifier.predict_one(comment, retries=0)
        if not answer:
            return None

        name, cosine, _accepted = categories.resolve(
            answer.get("category"),
            answer.get("confidence"),
            float(current_app.config["CLASSIFIER_COSINE_FLOOR"]),
        )
        db.upsert_category(
            spend_id, name, answer.get("confidence"), cosine, "knn"
        )
        return name
    except Exception:  # noqa: BLE001 - classification must never break a write
        current_app.logger.warning(
            "classifier failed for spend %s", spend_id, exc_info=True
        )
        return None


def _spend_payload(rows):
    return [
        {
            "date": _iso_date(r["spent_at"]),
            "amount": float(r["amount"]),
            "comment": r["comment"],
            "id": r["id"],
        }
        for r in rows
    ]


def _month_spends(start, end):
    return _spend_payload(db.list_spends_month(start, end))


def _ensure_categories(spends, refresh=False):
    """Classify rows with no stored category (or every non-manual one).

    Returns ``(assignments_by_spend_id, meta)``. Manual edits are never
    overwritten, not even by an explicit refresh.
    """
    stored = db.get_categories([s["id"] for s in spends]) if spends else {}
    meta = {"classified": 0, "failed": 0, "latencyMs": 0, "degraded": False}

    todo = []
    for spend in spends:
        have = stored.get(spend["id"])
        if have and have.get("source") == "manual":
            continue
        if have and not refresh:
            continue
        if not classifier.normalize_comment(spend["comment"]):
            db.upsert_category(
                spend["id"], categories.FALLBACK_CATEGORY, source="fallback"
            )
            stored[spend["id"]] = {
                "spend_id": spend["id"],
                "category": categories.FALLBACK_CATEGORY,
                "confidence": None,
                "cosine": None,
                "source": "fallback",
            }
            continue
        todo.append(spend)

    if not todo:
        return stored, meta

    if not classifier.is_enabled():
        meta["degraded"] = True
        meta["failed"] = len(todo)
        return stored, meta

    answers, info = classifier.predict_many([s["comment"] for s in todo])
    meta["latencyMs"] = info.get("latencyMs", 0)
    meta["degraded"] = bool(info.get("degraded"))

    floor = float(current_app.config["CLASSIFIER_COSINE_FLOOR"])
    for spend in todo:
        answer = answers.get(classifier.normalize_comment(spend["comment"]))
        if not answer:
            meta["failed"] += 1
            continue
        name, cosine, _accepted = categories.resolve(
            answer.get("category"), answer.get("confidence"), floor
        )
        db.upsert_category(spend["id"], name, answer.get("confidence"), cosine, "knn")
        stored[spend["id"]] = {
            "spend_id": spend["id"],
            "category": name,
            "confidence": answer.get("confidence"),
            "cosine": cosine,
            "source": "knn",
        }
        meta["classified"] += 1

    return stored, meta


def _report(month=None, refresh=False):
    start, end = categories.month_bounds(month)
    spends = _month_spends(start, end)
    assignments, meta = _ensure_categories(spends, refresh=refresh)

    buckets = categories.aggregate(
        spends,
        assignments,
        review_cosine=float(current_app.config["CLASSIFIER_REVIEW_COSINE"]),
    )
    total = round(sum(float(s["amount"]) for s in spends), 2)
    unique = len({classifier.normalize_comment(s["comment"]) for s in spends})
    metrics = categories.build_metrics(spends, buckets, meta, unique)

    return {
        "month": month or categories.month_key(start),
        "total": total,
        "byCategory": buckets,
        "summary": categories.build_summary(
            buckets, total, metrics["unclassified"], metrics["lowConfidence"]
        ),
        # The UI needs the closed list to offer a manual override.
        "categories": categories.CATEGORIES,
        "metrics": metrics,
        "classifier": {
            "enabled": classifier.is_enabled(),
            "cosineFloor": float(current_app.config["CLASSIFIER_COSINE_FLOOR"]),
            "reviewCosine": float(current_app.config["CLASSIFIER_REVIEW_COSINE"]),
        },
    }


# --- spends CRUD -----------------------------------------------------------


@bp.route("", methods=["GET"])
@require_auth
def list_spends():
    rows = db.list_spends()
    return jsonify([_serialize(r) for r in rows])


@bp.route("", methods=["POST"])
@require_auth
def create_spend():
    payload = request.get_json(silent=True) or {}
    parsed, error = _parse_payload(payload)
    if error:
        return jsonify({"error": error}), 400

    spent_at = parsed["spent_at"] or datetime.now()
    row = db.create_spend(parsed["amount"], parsed["comment"], spent_at)
    _classify_row(row["id"], parsed["comment"])
    return jsonify(_serialize(row)), 201


@bp.route("/<int:spend_id>", methods=["PUT"])
@require_auth
def update_spend(spend_id):
    payload = request.get_json(silent=True) or {}
    parsed, error = _parse_payload(payload)
    if error:
        return jsonify({"error": error}), 400

    spent_at = parsed["spent_at"] or datetime.now()
    row = db.update_spend(spend_id, parsed["amount"], parsed["comment"], spent_at)
    if row is None:
        return jsonify({"error": "not found"}), 404

    # A changed comment invalidates the old label — but a human override wins.
    existing = db.get_spend_category(spend_id)
    if not existing or existing.get("source") != "manual":
        _classify_row(spend_id, parsed["comment"])
    return jsonify(_serialize(row))


@bp.route("/<int:spend_id>", methods=["DELETE"])
@require_auth
def delete_spend(spend_id):
    deleted = db.delete_spend(spend_id)
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return "", 204


@bp.route("/<int:spend_id>/category", methods=["PATCH"])
@require_auth
def set_spend_category(spend_id):
    payload = request.get_json(silent=True) or {}
    name = payload.get("category")
    if not categories.is_valid(name):
        return (
            jsonify({"error": "category must be one of the supported names"}),
            400,
        )

    if db.get_spend(spend_id) is None:
        return jsonify({"error": "not found"}), 404

    normalized = categories.normalize_category(name)
    # `manual` also protects the row from being re-labelled by refresh/backfill.
    db.upsert_category(spend_id, normalized, source="manual")
    return jsonify({"id": spend_id, "category": normalized, "source": "manual"})


# --- category report -------------------------------------------------------


@stats_bp.route("", methods=["GET"])
@require_auth
def stats():
    month = request.args.get("month") or None
    if month and not _MONTH_RE.match(month):
        return jsonify({"error": "month must be in YYYY-MM format"}), 400
    refresh = (request.args.get("refresh") or "").lower() in ("1", "true", "yes")
    return jsonify(_report(month, refresh=refresh))


@stats_bp.route("/backfill", methods=["POST"])
@require_auth
def backfill():
    """Classify every history row that has no stored category yet."""
    try:
        limit = int(request.args.get("limit", "2000"))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    spends = _spend_payload(db.list_spends_without_category(limit=limit))
    if not spends:
        return jsonify({"scanned": 0, "classified": 0, "failed": 0, "degraded": False})

    _assignments, meta = _ensure_categories(spends)
    return jsonify(
        {
            "scanned": len(spends),
            "classified": meta["classified"],
            "failed": meta["failed"],
            "degraded": meta["degraded"],
        }
    )


# --- legacy AI blueprint ---------------------------------------------------


@ai_bp.route("/analyze", methods=["POST"])
@require_auth
def analyze():
    # `CATEGORIES_BACKEND=knn` (default) answers in the old shape so existing
    # callers keep working while the LLM path is being retired.
    if current_app.config["CATEGORIES_BACKEND"] != "llm":
        report = _report()
        metrics = report["metrics"]
        return jsonify(
            {
                "categories": report["byCategory"],
                "notice": report["summary"],
                "month": report["month"],
                "metrics": {
                    "rows": metrics["rows"],
                    "uniqueComments": metrics["uniqueComments"],
                    "totalRub": report["total"],
                    "latencyMs": metrics["latencyMs"],
                    "degraded": metrics["degraded"],
                },
            }
        )

    if not current_app.config["YANDEX_AI_API_KEY"]:
        return jsonify({"error": "AI is not configured"}), 503

    # Month-scoped read instead of pulling the whole history table.
    start, end = ai.month_bounds()
    rows = db.list_spends_month(start, end)
    spends = [
        {
            "date": _iso_date(r["spent_at"]),
            "amount": float(r["amount"]),
            "comment": r["comment"],
        }
        for r in rows
    ]

    system, user = ai._build_prompt(spends)
    limit = float(current_app.config.get("YANDEX_AI_MAX_COST_PER_REQUEST", "50"))
    max_tokens = int(current_app.config["YANDEX_AI_MODEL_MAX_TOKENS"])
    est = ai.estimate_request_cost(system, user, output_tokens=max_tokens)
    if est["cost_rub"] > limit:
        return (
            jsonify(
                {
                    "error": (
                        f"AI request would cost ~{est['cost_rub']:.2f} ₽, "
                        f"exceeding the {limit:.0f} ₽ per-request limit."
                    )
                }
            ),
            400,
        )

    try:
        result = ai.analyze_spends(spends)
    except Exception as exc:  # noqa: BLE001 - surface any AI/provider error
        return jsonify({"error": f"AI request failed: {exc}"}), 502

    cost = ai.compute_cost(
        result["usage"],
        current_app.config["YANDEX_AI_INPUT_PRICE_PER_1K"],
        current_app.config["YANDEX_AI_OUTPUT_PRICE_PER_1K"],
    )
    db.record_ai_usage(result["usage"]["input_tokens"], result["usage"]["output_tokens"], cost)

    grand_total = sum(float(s["amount"]) for s in spends)
    return jsonify(
        {
            "categories": result["categories"],
            "notice": result["notice"],
            "month": result["month"],
            "metrics": {
                "rows": len(spends),
                "uniqueComments": ai.unique_comment_count(spends),
                "estimatedInputTokens": est["input_tokens"],
                "estimatedOutputTokens": est["output_tokens"],
                "estimatedCost": est["cost_rub"],
                "actualCost": round(cost, 4),
                "totalRub": round(grand_total, 2),
                "maxOutputTokens": max_tokens,
            },
        }
    )


@ai_bp.route("/cost", methods=["GET"])
@require_auth
def ai_cost():
    total = db.get_ai_total_cost()
    daily_limit = current_app.config["YANDEX_AI_DAILY_LIMIT"]
    # `since` = epoch milliseconds of the user's local start-of-today.
    since = request.args.get("since")
    today = total
    if since:
        try:
            utc_boundary = datetime.fromtimestamp(
                int(since) / 1000.0, timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
            today = db.get_ai_cost_since(utc_boundary)
        except (TypeError, ValueError, OSError):
            today = total
    return jsonify(
        {
            "totalCostRub": total,
            "todayCostRub": round(today, 4),
            "dailyLimitRub": daily_limit,
        }
    )
