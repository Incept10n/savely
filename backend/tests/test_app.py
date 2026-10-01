import os
import tempfile
from datetime import datetime, timedelta

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
