from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from . import ai, db
from .auth import require_auth

bp = Blueprint("spends", __name__, url_prefix="/api/spends")
ai_bp = Blueprint("ai", __name__, url_prefix="/api/ai")


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
    return jsonify(_serialize(row))


@bp.route("/<int:spend_id>", methods=["DELETE"])
@require_auth
def delete_spend(spend_id):
    deleted = db.delete_spend(spend_id)
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return "", 204


@ai_bp.route("/analyze", methods=["POST"])
@require_auth
def analyze():
    if not current_app.config["YANDEX_AI_API_KEY"]:
        return jsonify({"error": "AI is not configured"}), 503

    rows = db.list_spends()
    spends = [
        {
            "date": _iso_date(r["spent_at"]),
            "amount": float(r["amount"]),
            "comment": r["comment"],
        }
        for r in rows
    ]

    month_spends = ai._month_spends(spends)
    system, user = ai._build_prompt(month_spends)
    limit = float(current_app.config.get("YANDEX_AI_MAX_COST_PER_REQUEST", "50"))
    est = ai.estimate_request_cost(
        system,
        user,
        output_tokens=int(current_app.config["YANDEX_AI_MODEL_MAX_TOKENS"]),
    )
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

    return jsonify(
        {
            "categories": result["categories"],
            "notice": result["notice"],
            "month": result["month"],
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
