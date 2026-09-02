from datetime import datetime

from flask import Blueprint, jsonify, request

from . import db
from .auth import require_auth

bp = Blueprint("spends", __name__, url_prefix="/api/spends")


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
