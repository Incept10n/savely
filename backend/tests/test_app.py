import io
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import create_app

AUTH = "iloveyme214365"
AUTH_HEADER = {"X-Auth-String": AUTH}


def _month_date(day=1):
    # a date inside the current calendar month
    today = datetime.now().replace(day=1)
    return today.replace(day=day).isoformat()


def _last_month_date(day=1):
    start = datetime.now().replace(day=1)
    prev = start - timedelta(days=1)
    return prev.replace(day=day).isoformat()


@pytest.fixture()
def app():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    app = create_app(
        {
            "TESTING": True,
            "AUTH_STRING": AUTH,
            "DB_TYPE": "sqlite",
            "DB_FILE": path,
            "INIT_DB": True,
            "YANDEX_AI_API_KEY": "test-key",
            "YANDEX_AI_FOLDER_ID": "folder-test",
            "YANDEX_AI_MODEL_URI": "gpt://folder-test/yandexgpt-5.1/latest",
            "YANDEX_AI_INPUT_PRICE_PER_1K": 0.8,
            "YANDEX_AI_OUTPUT_PRICE_PER_1K": 0.8,
            # The legacy AI tests below exercise the YandexGPT path on purpose.
            "CATEGORIES_BACKEND": "llm",
            # No classifier by default: no test may reach the network.
            "CLASSIFIER_URL": "",
        }
    )
    yield app
    os.unlink(path)


@pytest.fixture()
def client(app):
    return app.test_client()


def _post(client, data, headers=None):
    return client.post("/api/spends", json=data, headers=headers or AUTH_HEADER)


class TestAuth:
    def test_health_no_auth(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_spends_requires_auth(self, client):
        resp = client.get("/api/spends")
        assert resp.status_code == 401

    def test_wrong_auth_rejected(self, client):
        resp = client.get("/api/spends", headers={"X-Auth-String": "wrong"})
        assert resp.status_code == 401

    def test_empty_auth_rejected(self, client):
        resp = client.get("/api/spends", headers={"X-Auth-String": ""})
        assert resp.status_code == 401

    def test_auth_verify_ok(self, client):
        resp = client.get("/api/auth/verify", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


class TestSpends:
    def test_empty_list(self, client):
        resp = client.get("/api/spends", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_add_spend_default_date(self, client):
        resp = _post(client, {"amount": 150.50, "comment": "groceries"})
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["amount"] == 150.5
        assert body["comment"] == "groceries"
        assert "date" in body

    def test_add_spend_with_date(self, client):
        resp = _post(client, {"amount": 42, "comment": "coffee", "date": "2024-01-15T10:00:00"})
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["date"].startswith("2024-01-15")

    def test_add_spend_requires_amount(self, client):
        resp = _post(client, {"comment": "no amount"})
        assert resp.status_code == 400

    def test_add_spend_invalid_amount(self, client):
        resp = _post(client, {"amount": "abc", "comment": "x"})
        assert resp.status_code == 400

    def test_add_spend_negative_amount(self, client):
        resp = _post(client, {"amount": -5, "comment": "x"})
        assert resp.status_code == 400

    def test_list_orders_by_date_desc(self, client):
        _post(client, {"amount": 1, "comment": "old", "date": "2024-01-01T00:00:00"})
        _post(client, {"amount": 2, "comment": "new", "date": "2024-06-01T00:00:00"})
        rows = client.get("/api/spends", headers=AUTH_HEADER).get_json()
        assert rows[0]["comment"] == "new"

    def test_update_spend(self, client):
        created = _post(client, {"amount": 10, "comment": "a"}).get_json()
        resp = client.put(
            f"/api/spends/{created['id']}",
            json={"amount": 99, "comment": "b", "date": "2024-03-03T00:00:00"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["amount"] == 99.0
        assert body["comment"] == "b"
        assert body["date"].startswith("2024-03-03")

    def test_update_missing(self, client):
        resp = client.put(
            "/api/spends/99999",
            json={"amount": 1, "comment": "x"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404

    def test_delete_spend(self, client):
        created = _post(client, {"amount": 10, "comment": "a"}).get_json()
        resp = client.delete(f"/api/spends/{created['id']}", headers=AUTH_HEADER)
        assert resp.status_code == 204
        assert client.get("/api/spends", headers=AUTH_HEADER).get_json() == []

    def test_delete_missing(self, client):
        resp = client.delete("/api/spends/99999", headers=AUTH_HEADER)
        assert resp.status_code == 404


class TestAi:
    @pytest.fixture(autouse=True)
    def _reset_json_mode(self):
        import app.ai as ai

        # responseFormat support is cached per process; reset between tests.
        monkey = pytest.MonkeyPatch()
        monkey.setattr(ai, "_JSON_MODE", None)
        yield
        monkey.undo()

    def _mock_response(self, monkeypatch, text, usage=None):
        usage = usage or {
            "inputTextTokens": "100",
            "completionTokens": "50",
            "totalTokens": "150",
        }

        class FakeResp:
            def __init__(self, payload=None, status_code=200):
                self._payload = payload
                self.status_code = status_code

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests

                    raise requests.HTTPError(f"status {self.status_code}")

            def json(self):
                return self._payload

        self.FakeResp = FakeResp

        payload = {
            "result": {
                "alternatives": [
                    {
                        "message": {"role": "assistant", "text": text},
                        "status": "ALTERNATIVE_STATUS_FINAL",
                    }
                ],
                "usage": usage,
            }
        }

        import app.ai as ai

        monkeypatch.setattr(
            ai.requests, "post", lambda *a, **k: FakeResp(payload=payload)
        )
        monkeypatch.setattr(ai.time, "sleep", lambda *_: None)

    def test_analyze_requires_auth(self, client):
        resp = client.post("/api/ai/analyze")
        assert resp.status_code == 401

    def test_analyze_returns_categories_and_notice(self, client, monkeypatch):
        _post(client, {"amount": 150.5, "comment": "groceries"})
        _post(client, {"amount": 300, "comment": "taxi"})
        self._mock_response(
            monkeypatch,
            '{"categories":[{"name":"Продукты","indices":[0]}],'
            '"notice":"Всё отлично, лишнего не тратишь."}',
        )
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["categories"][0]["name"] == "Продукты"
        # index 0 maps back to the first (most recent) spend: the "taxi" one
        assert body["categories"][0]["spends"][0]["comment"] == "taxi"
        assert "Всё отлично" in body["notice"]
        assert body["month"]

    def test_analyze_with_existing_spends(self, client, monkeypatch):
        _post(client, {"amount": 150.5, "comment": "groceries"})
        _post(client, {"amount": 300, "comment": "taxi"})

        requests_calls = []

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "result": {
                        "alternatives": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "text": (
                                        '{"categories":[{"name":"А","indices":[0]}],'
                                        '"notice":"ok"}'
                                    ),
                                },
                                "status": "ALTERNATIVE_STATUS_FINAL",
                            }
                        ],
                        "usage": {
                            "inputTextTokens": "10",
                            "completionTokens": "10",
                        },
                    }
                }

        import app.ai as ai

        def fake_post(*args, **kwargs):
            requests_calls.append((args, kwargs))
            return FakeResp()

        monkeypatch.setattr(ai.requests, "post", fake_post)
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert resp.status_code == 200
        # both spends (amount + comment) must be sent to the model
        sent = requests_calls[0][1]["json"]
        user_text = sent["messages"][-1]["text"]
        assert "groceries" in user_text
        assert "taxi" in user_text

    def test_analyze_records_cost_and_cumulative(self, client, monkeypatch):
        self._mock_response(
            monkeypatch,
            '{"categories":[],"notice":"ok"}',
            usage={"inputTextTokens": "1000", "completionTokens": "1000"},
        )
        client.post("/api/ai/analyze", headers=AUTH_HEADER)
        cost = client.get("/api/ai/cost", headers=AUTH_HEADER).get_json()
        # 1000/1000 in + 1000/1000 out at 0.8/1k each = 0.8 + 0.8 = 1.6
        assert cost["totalCostRub"] == pytest.approx(1.6)

        # second call accumulates
        client.post("/api/ai/analyze", headers=AUTH_HEADER)
        cost = client.get("/api/ai/cost", headers=AUTH_HEADER).get_json()
        assert cost["totalCostRub"] == pytest.approx(3.2)

    def test_analyze_guard_blocks_expensive_request(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "coffee"})

        import app.ai as ai

        calls = []

        def fake_post(*args, **kwargs):
            calls.append(args)
            raise AssertionError("model must not be called when cost guard blocks")

        monkeypatch.setattr(ai.requests, "post", fake_post)
        # Force the estimate over any threshold so a normal request is blocked.
        from flask import current_app

        with client.application.app_context():
            current_app.config["YANDEX_AI_MAX_COST_PER_REQUEST"] = 0.0001

        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert resp.status_code == 400
        assert "exceeding" in resp.get_json()["error"]
        assert calls == []

    def test_estimate_request_cost(self, app):
        import app.ai as ai

        with app.app_context():
            est = ai.estimate_request_cost("system" * 100, "user" * 100, output_tokens=1000)
        # input tokens are non-zero and output budget is included
        assert est["input_tokens"] >= 1
        assert est["output_tokens"] == 1000
        assert est["cost_rub"] > 0

    def test_cost_requires_auth(self, client):
        resp = client.get("/api/ai/cost")
        assert resp.status_code == 401

    def test_cost_reports_daily_window(self, client, monkeypatch):
        # first record today
        self._mock_response(
            monkeypatch,
            '{"categories":[],"notice":"ok"}',
            usage={"inputTextTokens": "1000", "completionTokens": "1000"},
        )
        client.post("/api/ai/analyze", headers=AUTH_HEADER)

        # `since` = start of today (epoch ms). created_at default is UTC now,
        # so a since boundary in the past (e.g. yesterday) includes it.
        import time

        yesterday_ms = int((time.time() - 86400) * 1000)
        tomorrow_ms = int((time.time() + 86400) * 1000)

        resp = client.get(f"/api/ai/cost?since={yesterday_ms}", headers=AUTH_HEADER)
        body = resp.get_json()
        assert body["todayCostRub"] == pytest.approx(1.6)
        assert body["totalCostRub"] == pytest.approx(1.6)
        assert body["dailyLimitRub"] == 15

        # a since boundary in the future excludes today's record
        resp = client.get(f"/api/ai/cost?since={tomorrow_ms}", headers=AUTH_HEADER)
        assert resp.get_json()["todayCostRub"] == pytest.approx(0.0)

    def test_parse_result_handles_raw_control_characters(self):
        from app.ai import _escape_control_chars, _parse_result

        # a raw newline inside a notice value (as the model sometimes emits)
        text = ('{"categories":[{"name":"Кафе и рестораны","indices":[0,1]}],'
                '"notice":"line1\nline2"}')
        parsed = _parse_result(text)
        assert parsed["categories"][0]["indices"] == [0, 1]
        assert parsed["notice"] == "line1\nline2"

        import json

        escaped = _escape_control_chars(text)
        assert json.loads(escaped)["notice"] == "line1\nline2"

        # pretty-printed JSON (structural newlines) with a raw newline in a value
        pretty = '{\n  "categories": [],\n  "notice": "a\nb"\n}'
        assert _parse_result(pretty)["notice"] == "a\nb"

        # model wraps the JSON with prose before/after
        wrapped = 'Вот результат:\n{"categories":[],"notice":"ok"}\nНадеюсь, помог!'
        assert _parse_result(wrapped)["notice"] == "ok"

    def test_prompt_omits_dates(self, client, monkeypatch):
        _post(
            client,
            {"amount": 42, "comment": "кофе", "date": _month_date()},
        )
        self._mock_response(monkeypatch, '{"categories":[],"notice":"ok"}')

        sent = {}

        import app.ai as ai

        original = ai.requests.post

        def capture(*args, **kwargs):
            sent["body"] = kwargs["json"]
            return original(*args, **kwargs)

        monkeypatch.setattr(ai.requests, "post", capture)
        client.post("/api/ai/analyze", headers=AUTH_HEADER)

        user_text = sent["body"]["messages"][-1]["text"]
        assert "кофе" in user_text
        # dates are dead weight: rows come back by index, not by date
        assert _month_date()[:10] not in user_text

    def test_request_uses_zero_temperature_and_configured_max_tokens(
        self, client, monkeypatch
    ):
        _post(client, {"amount": 10, "comment": "x"})
        self._mock_response(monkeypatch, '{"categories":[],"notice":"ok"}')

        sent = {}

        import app.ai as ai

        original = ai.requests.post

        def capture(*args, **kwargs):
            sent["body"] = kwargs["json"]
            return original(*args, **kwargs)

        monkeypatch.setattr(ai.requests, "post", capture)
        client.post("/api/ai/analyze", headers=AUTH_HEADER)

        options = sent["body"]["completionOptions"]
        assert options["temperature"] == 0
        # guard and the real request must price the same output budget
        assert options["maxTokens"] == 300

    def test_only_current_month_is_sent(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "current", "date": _month_date(1)})
        _post(client, {"amount": 20, "comment": "lastmonth", "date": _last_month_date(1)})
        self._mock_response(
            monkeypatch,
            '{"categories":[{"name":"Продукты","indices":[0]}],"notice":"ок"}',
        )

        sent = {}

        import app.ai as ai

        original = ai.requests.post

        def capture(*args, **kwargs):
            sent["body"] = kwargs["json"]
            return original(*args, **kwargs)

        monkeypatch.setattr(ai.requests, "post", capture)
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)

        assert resp.status_code == 200
        user_text = sent["body"]["messages"][-1]["text"]
        assert "current" in user_text
        assert "lastmonth" not in user_text
        assert resp.get_json()["metrics"]["rows"] == 1

    def test_spends_endpoint_still_returns_all_months(self, client, monkeypatch):
        # the month scoping must not leak into the plain list endpoint
        _post(client, {"amount": 10, "comment": "a", "date": _month_date(1)})
        _post(client, {"amount": 20, "comment": "b", "date": _last_month_date(1)})
        rows = client.get("/api/spends", headers=AUTH_HEADER).get_json()
        assert {r["comment"] for r in rows} == {"a", "b"}

    def test_category_totals_computed_server_side(self, client, monkeypatch):
        _post(client, {"amount": 100, "comment": "продукты"})
        _post(client, {"amount": 250.5, "comment": "продукты"})
        _post(client, {"amount": 49.5, "comment": "такси"})
        self._mock_response(
            monkeypatch,
            '{"categories":[{"name":"Продукты","indices":[1,2]},'
            '{"name":"Транспорт и такси","indices":[0]}],"notice":"ок"}',
        )
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        cats = {c["name"]: c for c in resp.get_json()["categories"]}

        food = cats["Продукты"]
        assert food["total"] == 350.5
        assert food["count"] == 2
        assert food["share"] == pytest.approx(87.6, abs=0.05)
        assert cats["Транспорт и такси"]["total"] == 49.5
        assert cats["Транспорт и такси"]["share"] == pytest.approx(12.4, abs=0.05)
        # the model never does arithmetic, so the buckets must add up
        assert sum(c["total"] for c in cats.values()) == 400.0

    def test_categories_sorted_by_total_desc(self, client, monkeypatch):
        for amount, comment in ((10, "a"), (500, "b"), (100, "c")):
            _post(client, {"amount": amount, "comment": comment})
        self._mock_response(
            monkeypatch,
            '{"categories":[{"name":"Продукты","indices":[0]},'
            '{"name":"Путешествия","indices":[1]},'
            '{"name":"Одежда и обувь","indices":[2]}],"notice":"ок"}',
        )
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        names = [c["name"] for c in resp.get_json()["categories"]]
        assert names == ["Путешествия", "Продукты", "Одежда и обувь"]

    def test_invented_category_becomes_other(self, client, monkeypatch):
        _post(client, {"amount": 100, "comment": "подписка на криптобиржу"})
        self._mock_response(
            monkeypatch,
            '{"categories":[{"name":"Криптовалюта","indices":[0]}],"notice":"ок"}',
        )
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        cats = resp.get_json()["categories"]
        assert [c["name"] for c in cats] == ["Другое"]
        assert cats[0]["total"] == 100

    def test_decorated_category_name_is_mapped(self, client, monkeypatch):
        _post(client, {"amount": 100, "comment": "пятёрочка"})
        self._mock_response(
            monkeypatch,
            '{"categories":[{"name":"Продукты / супермаркет","indices":[0]}],'
            '"notice":"ок"}',
        )
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert [c["name"] for c in resp.get_json()["categories"]] == ["Продукты"]

    def test_skipped_and_bad_indices_land_in_other(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "one"})
        _post(client, {"amount": 20, "comment": "two"})
        _post(client, {"amount": 30, "comment": "three"})
        # model covers index 0, points at a nonexistent index, and skips 2
        self._mock_response(
            monkeypatch,
            '{"categories":[{"name":"Продукты","indices":[0,999]}],'
            '"notice":"ок"}',
        )
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        cats = {c["name"]: c for c in resp.get_json()["categories"]}

        assert cats["Продукты"]["count"] == 1
        assert cats["Продукты"]["total"] == 30
        # no spend may silently disappear
        assert cats["Другое"]["count"] == 2
        assert cats["Другое"]["total"] == 30
        total_rows = sum(c["count"] for c in cats.values())
        assert total_rows == 3

    def test_metrics_expose_uniqueness_ratio(self, client, monkeypatch):
        for comment in ("Кофе", "кофе ", "Такси", "пятёрочка"):
            _post(client, {"amount": 10, "comment": comment})
        self._mock_response(monkeypatch, '{"categories":[],"notice":"ok"}')
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        metrics = resp.get_json()["metrics"]

        assert metrics["rows"] == 4
        # case/whitespace-insensitive, so "Кофе" and "кофе " collapse
        assert metrics["uniqueComments"] == 3
        assert metrics["estimatedInputTokens"] > 0
        assert metrics["maxOutputTokens"] == 300
        assert metrics["totalRub"] == 40

    def test_empty_categorization_still_returns_all_spends(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "one"})
        _post(client, {"amount": 20, "comment": "two"})
        self._mock_response(monkeypatch, '{"categories":[],"notice":""}')
        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        cats = resp.get_json()["categories"]
        assert [c["name"] for c in cats] == ["Другое"]
        assert cats[0]["total"] == 30

    def test_retries_on_throttling(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "x"})

        import app.ai as ai

        attempts = []

        class FakeResp:
            def __init__(self, status_code):
                self.status_code = status_code

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests

                    raise requests.HTTPError(f"status {self.status_code}")

            def json(self):
                return {
                    "result": {
                        "alternatives": [
                            {"message": {"text": '{"categories":[],"notice":"ok"}'}}
                        ],
                        "usage": {"inputTextTokens": "10", "completionTokens": "10"},
                    }
                }

        def flaky_post(*args, **kwargs):
            attempts.append(kwargs["json"])
            return FakeResp(429 if len(attempts) == 1 else 200)

        monkeypatch.setattr(ai.requests, "post", flaky_post)
        monkeypatch.setattr(ai.time, "sleep", lambda *_: None)

        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert len(attempts) == 2

    def test_client_error_is_not_retried(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "x"})

        from flask import current_app

        with client.application.app_context():
            current_app.config["YANDEX_AI_RESPONSE_FORMAT"] = False

        import app.ai as ai

        attempts = []

        class FakeResp:
            status_code = 400

            def raise_for_status(self):
                import requests

                raise requests.HTTPError("status 400")

            def json(self):
                return {}

        monkeypatch.setattr(
            ai.requests,
            "post",
            lambda *a, **k: (attempts.append(1), FakeResp())[1],
        )
        monkeypatch.setattr(ai.time, "sleep", lambda *_: None)

        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert resp.status_code == 502
        assert len(attempts) == 1

    def test_response_format_sent_when_enabled(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "x"})
        self._mock_response(monkeypatch, '{"categories":[],"notice":"ok"}')

        sent = {}

        import app.ai as ai

        original = ai.requests.post

        def capture(*args, **kwargs):
            sent["body"] = kwargs["json"]
            return original(*args, **kwargs)

        monkeypatch.setattr(ai.requests, "post", capture)
        client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert sent["body"]["responseFormat"] == {"type": "JSON_OBJECT"}

    def test_response_format_dropped_when_rejected(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "x"})

        import app.ai as ai

        attempts = []

        class FakeResp:
            def __init__(self, status_code):
                self.status_code = status_code

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests

                    raise requests.HTTPError(f"status {self.status_code}")

            def json(self):
                return {
                    "result": {
                        "alternatives": [
                            {"message": {"text": '{"categories":[],"notice":"ok"}'}}
                        ],
                        "usage": {"inputTextTokens": "10", "completionTokens": "10"},
                    }
                }

        def post(*args, **kwargs):
            body = kwargs["json"]
            attempts.append(body)
            # 400 only while responseFormat is present
            return FakeResp(400 if "responseFormat" in body else 200)

        monkeypatch.setattr(ai.requests, "post", post)
        monkeypatch.setattr(ai.time, "sleep", lambda *_: None)

        resp = client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert len(attempts) == 2
        assert "responseFormat" not in attempts[1]
        # support is remembered for the rest of the process
        assert ai._JSON_MODE is False

    def test_response_format_can_be_disabled(self, client, monkeypatch):
        _post(client, {"amount": 10, "comment": "x"})
        self._mock_response(monkeypatch, '{"categories":[],"notice":"ok"}')

        from flask import current_app

        with client.application.app_context():
            current_app.config["YANDEX_AI_RESPONSE_FORMAT"] = False

        sent = {}

        import app.ai as ai

        original = ai.requests.post

        def capture(*args, **kwargs):
            sent["body"] = kwargs["json"]
            return original(*args, **kwargs)

        monkeypatch.setattr(ai.requests, "post", capture)
        client.post("/api/ai/analyze", headers=AUTH_HEADER)
        assert "responseFormat" not in sent["body"]

    def test_normalize_category(self):
        from app.ai import normalize_category

        assert normalize_category("Продукты") == "Продукты"
        assert normalize_category("  продукты ") == "Продукты"
        assert normalize_category("ПРОДУКТЫ") == "Продукты"
        assert normalize_category("Продукты/супермаркет") == "Продукты"
        assert normalize_category("Кафе и рестораны") == "Кафе и рестораны"
        assert normalize_category("Нонсенс") == "Другое"
        assert normalize_category(None) == "Другое"
        assert normalize_category("") == "Другое"

    def test_month_bounds(self):
        from datetime import datetime

        from app.ai import month_bounds

        start, end = month_bounds(datetime(2026, 10, 15, 13, 45, 12))
        assert start == datetime(2026, 10, 1)
        assert end == datetime(2026, 11, 1)

        # December must roll over to the next year
        start, end = month_bounds(datetime(2026, 12, 31, 23, 59))
        assert start == datetime(2026, 12, 1)
        assert end == datetime(2027, 1, 1)

    def test_spent_at_index_exists(self, app):
        with app.app_context():
            from app import db

            conn = db._connection()
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'idx_spends_spent_at'"
                ).fetchall()
            finally:
                conn.close()
        assert len(rows) == 1

    def test_init_db_is_idempotent(self, app):
        with app.app_context():
            from app import db

            db.init_db()
            db.init_db()


class FakeClassifier:
    """Stand-in for `app.classifier` so no test touches the network."""

    def __init__(self):
        self.answers = {}
        self.down = False
        self.calls = []

    @staticmethod
    def key(comment):
        return " ".join((comment or "").strip().casefold().split())

    def predict_one(self, word, retries=None):
        self.calls.append(word)
        if self.down:
            return None
        return self.answers.get(self.key(word))

    def predict_many(self, comments):
        keys = {}
        for comment in comments:
            normalized = self.key(comment)
            if normalized:
                keys.setdefault(normalized, comment)
        self.calls.extend(keys.values())
        if self.down:
            return {}, {
                "unique": len(keys),
                "failed": len(keys),
                "latencyMs": 3,
                "degraded": True,
            }
        results = {k: self.answers[k] for k in keys if k in self.answers}
        return results, {
            "unique": len(keys),
            "failed": len(keys) - len(results),
            "latencyMs": 7,
            "degraded": not results,
        }


# confidence as reported by the service: ((cos + 1) / 2) * 100
def conf(cosine):
    return round((cosine + 1) / 2 * 100, 2)


@pytest.fixture()
def knn(app, monkeypatch):
    import app.classifier as classifier

    fake = FakeClassifier()
    app.config["CATEGORIES_BACKEND"] = "knn"
    app.config["CLASSIFIER_URL"] = "http://classifier.test"
    monkeypatch.setattr(classifier, "predict_one", fake.predict_one)
    monkeypatch.setattr(classifier, "predict_many", fake.predict_many)
    monkeypatch.setattr(classifier, "is_enabled", lambda: True)
    return fake


class TestCategoryReport:
    def test_stats_requires_auth(self, client):
        assert client.get("/api/stats").status_code == 401

    def test_disabled_classifier_still_reports_every_spend(self, client):
        _post(client, {"amount": 100, "comment": "продукты"})
        _post(client, {"amount": 50, "comment": "такси"})
        body = client.get("/api/stats", headers=AUTH_HEADER).get_json()

        assert [c["name"] for c in body["byCategory"]] == ["Другое"]
        assert body["total"] == 150
        assert body["byCategory"][0]["total"] == 150
        assert body["metrics"]["unclassified"] == 2
        assert body["metrics"]["degraded"] is True

    def test_aggregates_by_category(self, knn, client):
        knn.answers = {
            "продукты": {"category": "Продукты", "confidence": conf(0.92)},
            "такси": {"category": "Транспорт и такси", "confidence": conf(0.94)},
        }
        _post(client, {"amount": 100, "comment": "продукты"})
        _post(client, {"amount": 250.5, "comment": "продукты"})
        _post(client, {"amount": 49.5, "comment": "такси"})

        body = client.get("/api/stats", headers=AUTH_HEADER).get_json()
        cats = {c["name"]: c for c in body["byCategory"]}

        assert cats["Продукты"]["total"] == 350.5
        assert cats["Продукты"]["count"] == 2
        assert cats["Продукты"]["share"] == pytest.approx(87.6, abs=0.05)
        assert cats["Продукты"]["avg"] == 175.25
        assert cats["Транспорт и такси"]["share"] == pytest.approx(12.4, abs=0.05)
        # buckets must add up to the month total, no money may vanish
        assert sum(c["total"] for c in cats.values()) == body["total"] == 400.0
        # sorted by amount, biggest first
        assert [c["name"] for c in body["byCategory"]] == [
            "Продукты",
            "Транспорт и такси",
        ]
        assert body["metrics"]["rows"] == 3
        assert "Top category" in body["summary"]

    def test_second_report_uses_stored_categories(self, knn, client):
        knn.answers = {"продукты": {"category": "Продукты", "confidence": conf(0.9)}}
        _post(client, {"amount": 10, "comment": "продукты"})

        client.get("/api/stats", headers=AUTH_HEADER)
        knn.calls.clear()
        client.get("/api/stats", headers=AUTH_HEADER)
        # the classifier is asked once per unique comment, then the DB answers
        assert knn.calls == []

    def test_duplicate_comments_classified_once(self, knn, client):
        knn.answers = {"пятёрочка": {"category": "Продукты", "confidence": conf(0.9)}}
        for _ in range(3):
            _post(client, {"amount": 10, "comment": "Пятёрочка "})
        knn.calls.clear()
        client.get("/api/stats", headers=AUTH_HEADER)
        assert len([c for c in knn.calls if c.strip().lower() == "пятёрочка"]) <= 1

    def test_low_similarity_becomes_other(self, knn, client):
        # cos 0.60 is under CLASSIFIER_COSINE_FLOOR (0.80): the service's own
        # 0.40 cut-off would have accepted this label.
        knn.answers = {"невалинк": {"category": "Продукты", "confidence": conf(0.60)}}
        _post(client, {"amount": 100, "comment": "невалинк"})
        body = client.get("/api/stats", headers=AUTH_HEADER).get_json()

        assert [c["name"] for c in body["byCategory"]] == ["Другое"]
        assert body["byCategory"][0]["spends"][0]["cosine"] == pytest.approx(0.6)
        assert body["metrics"]["lowConfidence"] == 1

    def test_off_list_label_folds_to_other(self, knn, client):
        knn.answers = {
            "криптобиржа": {"category": "Криптовалюта", "confidence": conf(0.95)}
        }
        _post(client, {"amount": 100, "comment": "криптобиржа"})
        body = client.get("/api/stats", headers=AUTH_HEADER).get_json()
        assert [c["name"] for c in body["byCategory"]] == ["Другое"]

    def test_classifier_down_keeps_saved_categories(self, knn, client):
        knn.answers = {"продукты": {"category": "Продукты", "confidence": conf(0.9)}}
        _post(client, {"amount": 100, "comment": "продукты"})
        client.get("/api/stats", headers=AUTH_HEADER)

        knn.down = True
        _post(client, {"amount": 50, "comment": "новое"})
        body = client.get("/api/stats", headers=AUTH_HEADER).get_json()

        cats = {c["name"]: c for c in body["byCategory"]}
        assert cats["Продукты"]["total"] == 100
        assert cats["Другое"]["total"] == 50
        assert body["metrics"]["degraded"] is True
        assert body["metrics"]["unclassified"] == 1

    def test_manual_override_survives_refresh(self, knn, client):
        knn.answers = {"продукты": {"category": "Продукты", "confidence": conf(0.9)}}
        created = _post(client, {"amount": 100, "comment": "продукты"}).get_json()

        resp = client.patch(
            f"/api/spends/{created['id']}/category",
            json={"category": "Путешествия"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.get_json()["source"] == "manual"

        body = client.get("/api/stats?refresh=1", headers=AUTH_HEADER).get_json()
        assert [c["name"] for c in body["byCategory"]] == ["Путешествия"]
        assert body["metrics"]["lowConfidence"] == 0

    def test_manual_override_rejects_unknown_category(self, client):
        created = _post(client, {"amount": 100, "comment": "x"}).get_json()
        resp = client.patch(
            f"/api/spends/{created['id']}/category",
            json={"category": "Криптовалюта"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 400

    def test_override_missing_spend_is_404(self, client):
        resp = client.patch(
            "/api/spends/9999/category",
            json={"category": "Продукты"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404

    def test_create_classifies_immediately(self, knn, app, client):
        knn.answers = {"такси": {"category": "Транспорт и такси", "confidence": conf(0.93)}}
        created = _post(client, {"amount": 300, "comment": "такси"}).get_json()
        with app.app_context():
            from app import db

            assert db.get_spend_category(created["id"])["category"] == "Транспорт и такси"

    def test_update_comment_reclassifies(self, knn, app, client):
        knn.answers = {
            "продукты": {"category": "Продукты", "confidence": conf(0.9)},
            "кино": {"category": "Развлечения и подписки", "confidence": conf(0.9)},
        }
        created = _post(client, {"amount": 10, "comment": "продукты"}).get_json()
        client.put(
            f"/api/spends/{created['id']}",
            json={"amount": 10, "comment": "кино"},
            headers=AUTH_HEADER,
        )
        with app.app_context():
            from app import db

            assert (
                db.get_spend_category(created["id"])["category"]
                == "Развлечения и подписки"
            )

    def test_delete_spend_drops_category(self, knn, app, client):
        knn.answers = {"продукты": {"category": "Продукты", "confidence": conf(0.9)}}
        created = _post(client, {"amount": 10, "comment": "продукты"}).get_json()
        client.delete(f"/api/spends/{created['id']}", headers=AUTH_HEADER)
        with app.app_context():
            from app import db

            assert db.get_spend_category(created["id"]) is None

    def test_month_param_scopes_the_report(self, knn, client):
        knn.answers = {"продукты": {"category": "Продукты", "confidence": conf(0.9)}}
        _post(client, {"amount": 10, "comment": "продукты", "date": _month_date(1)})
        _post(client, {"amount": 20, "comment": "продукты", "date": _last_month_date(1)})

        last_month = datetime.now().replace(day=1) - timedelta(days=1)
        key = last_month.strftime("%Y-%m")
        body = client.get(f"/api/stats?month={key}", headers=AUTH_HEADER).get_json()
        assert body["month"] == key
        assert body["metrics"]["rows"] == 1
        assert body["total"] == 20

    def test_invalid_month_rejected(self, client):
        assert client.get("/api/stats?month=2026-13", headers=AUTH_HEADER).status_code == 400
        assert client.get("/api/stats?month=ja", headers=AUTH_HEADER).status_code == 400

    def test_backfill_classifies_history(self, knn, client):
        knn.down = True
        _post(client, {"amount": 10, "comment": "продукты"})
        _post(client, {"amount": 20, "comment": "такси"})
        assert client.get("/api/stats", headers=AUTH_HEADER).get_json()["metrics"]["unclassified"] == 2

        knn.down = False
        knn.answers = {
            "продукты": {"category": "Продукты", "confidence": conf(0.9)},
            "такси": {"category": "Транспорт и такси", "confidence": conf(0.9)},
        }
        body = client.post("/api/stats/backfill", headers=AUTH_HEADER).get_json()
        assert body == {
            "scanned": 2,
            "classified": 2,
            "failed": 0,
            "degraded": False,
        }
        stats = client.get("/api/stats", headers=AUTH_HEADER).get_json()
        assert stats["metrics"]["unclassified"] == 0

    def test_empty_month_report(self, client):
        body = client.get("/api/stats", headers=AUTH_HEADER).get_json()
        assert body["byCategory"] == []
        assert body["total"] == 0
        assert body["metrics"]["rows"] == 0

    def test_analyze_answers_from_classifier_by_default(self, knn, client):
        knn.answers = {"продукты": {"category": "Продукты", "confidence": conf(0.9)}}
        _post(client, {"amount": 10, "comment": "продукты"})
        body = client.post("/api/ai/analyze", headers=AUTH_HEADER).get_json()
        assert [c["name"] for c in body["categories"]] == ["Продукты"]
        assert body["notice"]
        assert "estimatedCost" not in body["metrics"]


class TestMonthBounds:
    def test_bounds_for_explicit_month(self):
        from app import categories

        start, end = categories.month_bounds("2026-02")
        assert start.isoformat() == "2026-02-01T00:00:00"
        assert end.isoformat() == "2026-03-01T00:00:00"

    def test_bounds_cross_the_year(self):
        from app import categories

        start, end = categories.month_bounds("2026-12")
        assert (start.year, start.month) == (2026, 12)
        assert (end.year, end.month) == (2027, 1)

    def test_bad_month_raises(self):
        from app import categories

        with pytest.raises(ValueError):
            categories.month_bounds("2026-19")


class TestConfidenceConversion:
    def test_round_trip(self):
        from app import categories

        assert categories.confidence_to_cosine(conf(0.9)) == pytest.approx(0.9)
        assert categories.confidence_to_cosine(None) is None
        assert categories.confidence_to_cosine("nope") is None

    def test_floor_replaces_label(self):
        from app import categories

        name, cosine, accepted = categories.resolve("Продукты", conf(0.5), 0.8)
        assert name == "Другое"
        assert accepted is False
        assert cosine == pytest.approx(0.5)

        name, _cosine, accepted = categories.resolve("Продукты", conf(0.9), 0.8)
        assert name == "Продукты"
        assert accepted is True


class _PredictHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for classifier/knn.py's POST /predict."""

    status = 200
    payload = {"category": "Продукты", "confidence": 96.0}
    calls = []

    def do_POST(self):  # noqa: N802 - stdlib API
        length = int(self.headers.get("Content-Length") or 0)
        self.calls.append(json.loads(self.rfile.read(length)))
        body = json.dumps(self.payload).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        pass


class TestClassifierClient:
    @pytest.fixture()
    def server(self, app):
        handler = type("Handler", (_PredictHandler,), {"calls": []})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        app.config["CLASSIFIER_URL"] = f"http://127.0.0.1:{httpd.server_address[1]}"
        app.config["CLASSIFIER_MAX_RETRIES"] = 0
        yield handler, httpd.server_address[1]
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    def test_predict_one_over_http(self, app, server):
        from app import classifier

        handler, _port = server
        with app.app_context():
            assert classifier.predict_one("Пятёрочка") == {
                "category": "Продукты",
                "confidence": 96.0,
            }
        # the service expects the raw text, it normalizes on its own side
        assert handler.calls == [{"word": "Пятёрочка"}]

    def test_predict_many_dedupes_comments(self, app, server):
        from app import classifier

        handler, _port = server
        with app.app_context():
            results, meta = classifier.predict_many(["Пятёрочка", "пятёрочка ", "такси"])

        assert set(results) == {"пятёрочка", "такси"}
        assert meta["unique"] == 2
        assert meta["failed"] == 0
        assert meta["degraded"] is False
        # one probe + one more request for the remaining unique comment
        assert len(handler.calls) == 2

    def test_503_marks_degraded(self, app, server):
        from app import classifier

        handler, _port = server
        handler.status = 503
        with app.app_context():
            results, meta = classifier.predict_many(["продукты", "такси"])
        assert results == {}
        assert meta["degraded"] is True
        assert meta["failed"] == 2

    def test_unreachable_service_is_not_an_exception(self, app):
        from app import classifier

        app.config["CLASSIFIER_URL"] = "http://127.0.0.1:1"
        app.config["CLASSIFIER_MAX_RETRIES"] = 0
        with app.app_context():
            assert classifier.predict_one("x") is None
            results, meta = classifier.predict_many(["x", "y"])
        assert results == {}
        assert meta["degraded"] is True

    def test_empty_comments_never_reach_the_service(self, app, server):
        from app import classifier

        handler, _port = server
        with app.app_context():
            results, meta = classifier.predict_many(["", "   "])
        assert results == {}
        assert meta["unique"] == 0
        assert handler.calls == []


class TestManualLabels:
    """The training set for the weekly S3 export."""

    def _create(self, app, comment, source):
        from app import db

        with app.app_context():
            row = db.create_spend(10, comment, datetime(2026, 10, 1, 12, 0, 0))
            db.upsert_category(row["id"], "Продукты", 97.0, 0.94, source=source)
            return row["id"]

    def test_returns_only_manual_labels(self, app):
        from app import db

        self._create(app, "пятёрочка", "manual")
        self._create(app, "такси", "knn")
        self._create(app, "аптека", "fallback")
        with app.app_context():
            assert [r["comment"] for r in db.list_manual_labels()] == ["пятёрочка"]

    def test_skips_empty_comments(self, app):
        from app import db

        self._create(app, "", "manual")
        self._create(app, "   ", "manual")
        with app.app_context():
            assert db.list_manual_labels() == []

    def test_carries_amount_date_and_category(self, app):
        from app import db

        self._create(app, "лампа", "manual")
        with app.app_context():
            row = db.list_manual_labels()[0]
        assert row["category"] == "Продукты"
        assert row["amount"] == 10

    def test_date_formatting_covers_both_drivers(self):
        import dataset_export as dx

        # MySQL hands back a datetime, SQLite a string
        assert dx.format_date(datetime(2026, 10, 1, 12, 0)) == "01.10.2026"
        assert dx.format_date("2026-10-01 12:00:00") == "01.10.2026"
        assert dx.format_date("") == ""
        assert dx.format_date(None) == ""


class FakeS3:
    """Just enough of a boto3 client for `read_existing`."""

    def __init__(self, body=None):
        self.body = body

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 signature
        if self.body is None:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.body)}


class TestDatasetExport:
    def test_parses_the_real_file_format(self):
        import dataset_export as dx

        body = (
            "\ufeffdate;comment;amount;category\n"
            "17.10.2026;горшочек;147.00;Товары для дома\n"
            "16.10.2026;булочка;94.00;Кафе и рестораны\n"
        ).encode()
        rows = dx.read_existing(FakeS3(body), "b", "k")
        assert set(rows) == {"горшочек", "булочка"}
        assert rows["горшочек"]["category"] == "Товары для дома"
        assert rows["горшочек"]["date"] == "17.10.2026"

    def test_missing_object_is_not_an_error(self):
        import dataset_export as dx

        assert dx.read_existing(FakeS3(), "b", "k") == {}

    def test_merge_never_shrinks_and_manual_wins(self):
        import dataset_export as dx

        rows = {
            "горшочек": {"date": "", "comment": "горшочек", "amount": "1.00", "category": "Другое"},
        }
        labels = [
            {"comment": "Горшочек", "amount": 5, "spent_at": datetime(2026, 10, 1), "category": "Товары для дома"},
            {"comment": "такси", "amount": 300, "spent_at": datetime(2026, 10, 2), "category": "Транспорт и такси"},
        ]
        merged, added, updated = dx.merge(rows, labels)
        assert (added, updated) == (1, 1)
        assert merged["горшочек"]["category"] == "Товары для дома"
        assert merged["горшочек"]["date"] == "01.10.2026"
        assert merged["такси"]["amount"] == "300.00"

    def test_same_label_twice_changes_nothing(self):
        import dataset_export as dx

        labels = [
            {"comment": "кино", "amount": 400, "spent_at": datetime(2026, 10, 3), "category": "Развлечения и подписки"},
        ]
        rows = {}
        rows, added, _updated = dx.merge(rows, labels)
        assert added == 1
        rows, added, updated = dx.merge(rows, labels)
        assert (added, updated) == (0, 0)

    def test_rendered_file_round_trips(self):
        import dataset_export as dx

        labels = [
            {"comment": "пятёрочка", "amount": 123.4, "spent_at": datetime(2026, 10, 1), "category": "Продукты"},
        ]
        rows, _a, _u = dx.merge({}, labels)
        payload = dx.render(rows)
        assert payload.startswith(b"date;comment;amount;category\n")
        back = dx.read_existing(FakeS3(payload), "b", "k")
        assert back["пятёрочка"]["category"] == "Продукты"
        assert back["пятёрочка"]["amount"] == "123.40"

    def test_refuses_a_tiny_new_dataset(self):
        import dataset_export as dx

        assert dx.should_upload(False, {}) is False
        assert dx.should_upload(False, {str(i): i for i in range(5)}) is False
        # an existing object is always safe to merge into, however small
        assert dx.should_upload(True, {"x": 1}) is True
