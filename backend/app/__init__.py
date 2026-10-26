import os

from flask import Flask, jsonify

from . import db


def create_app(test_config=None):
    app = Flask(__name__)

    app.config.from_mapping(
        AUTH_STRING=os.environ.get("AUTH_STRING", "changeme"),
        DB_TYPE=os.environ.get("DB_TYPE", "mysql"),
        DB_FILE=os.environ.get("DB_FILE", ""),
        DB_HOST=os.environ.get("DB_HOST", "127.0.0.1"),
        DB_PORT=int(os.environ.get("DB_PORT", "3306")),
        DB_USER=os.environ.get("DB_USER", "savely"),
        DB_PASSWORD=os.environ.get("DB_PASSWORD", ""),
        DB_NAME=os.environ.get("DB_NAME", "savely"),
        INIT_DB=os.environ.get("INIT_DB", "1") not in ("0", "false", ""),
        YANDEX_AI_API_KEY=os.environ.get("YANDEX_AI_API_KEY", ""),
        YANDEX_AI_FOLDER_ID=os.environ.get("YANDEX_AI_FOLDER_ID", ""),
        YANDEX_AI_MODEL_URI=os.environ.get(
            "YANDEX_AI_MODEL_URI",
            "gpt://b1g22vmvppgsen3ogkj9/yandexgpt-5.1/latest",
        ),
        YANDEX_AI_INPUT_PRICE_PER_1K=float(os.environ.get("YANDEX_AI_INPUT_PRICE_PER_1K", "0.8")),
        YANDEX_AI_OUTPUT_PRICE_PER_1K=float(os.environ.get("YANDEX_AI_OUTPUT_PRICE_PER_1K", "0.8")),
        # Output token budget: sets completionOptions.maxTokens AND the
        # pre-request cost estimate, so the two can never drift apart.
        YANDEX_AI_MODEL_MAX_TOKENS=int(os.environ.get("YANDEX_AI_MODEL_MAX_TOKENS", "300")),
        # Ask the provider for JSON output. Falls back automatically to plain
        # text mode if the model rejects responseFormat.
        YANDEX_AI_RESPONSE_FORMAT=os.environ.get("YANDEX_AI_RESPONSE_FORMAT", "1")
        not in ("0", "false", ""),
        YANDEX_AI_MAX_COST_PER_REQUEST=float(os.environ.get("YANDEX_AI_MAX_COST_PER_REQUEST", "50")),
        YANDEX_AI_DAILY_LIMIT=float(os.environ.get("YANDEX_AI_DAILY_LIMIT", "15")),
        # Which engine fills the category report: the local kNN classifier
        # (`classifier/`, free and deterministic) or the legacy YandexGPT path.
        CATEGORIES_BACKEND=os.environ.get("CATEGORIES_BACKEND", "knn").strip().lower(),
        # Empty URL = classification disabled: spends are still saved, the
        # report simply shows them under «Другое» until a service is wired up.
        CLASSIFIER_URL=os.environ.get("CLASSIFIER_URL", ""),
        CLASSIFIER_TIMEOUT=float(os.environ.get("CLASSIFIER_TIMEOUT", "3")),
        CLASSIFIER_MAX_RETRIES=int(os.environ.get("CLASSIFIER_MAX_RETRIES", "1")),
        CLASSIFIER_CONCURRENCY=int(os.environ.get("CLASSIFIER_CONCURRENCY", "8")),
        # The service reports ((cos + 1) / 2) * 100; we invert it and decide
        # here. Below the floor the spend becomes «Другое», below the review
        # line it stays but is flagged for a human to confirm.
        CLASSIFIER_COSINE_FLOOR=float(os.environ.get("CLASSIFIER_COSINE_FLOOR", "0.80")),
        CLASSIFIER_REVIEW_COSINE=float(os.environ.get("CLASSIFIER_REVIEW_COSINE", "0.86")),
    )

    if test_config is not None:
        app.config.update(test_config)

    from .auth import require_auth
    from .routes import ai_bp, bp, stats_bp

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/auth/verify")
    @require_auth
    def verify():
        return jsonify({"ok": True})

    app.register_blueprint(bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(stats_bp)

    if app.config["INIT_DB"]:
        with app.app_context():
            db.init_db()

    return app


def get_app():
    return create_app()
