import os
import tempfile

import pytest

from app import create_app

AUTH = "iloveonyme214365"
AUTH_HEADER = {"X-Auth-String": AUTH}


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
    def _mock_response(self, monkeypatch, text, usage=None):
        usage = usage or {
            "inputTextTokens": "100",
            "completionTokens": "50",
            "totalTokens": "150",
        }

        class FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
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

        monkeypatch.setattr(ai.requests, "post", lambda *a, **k: FakeResp())

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

    def test_parse_result_handles_raw_control_characters(self):
        from app.ai import _escape_control_chars, _parse_result

        # a raw newline inside a comment value (as the model sometimes emits)
        text = ('{"categories":[{"name":"Кафе","spends":[{"amount":150.5,'
                '"comment":"line1\nline2"}]}],"notice":"ok"}')
        parsed = _parse_result(text)
        assert parsed["categories"][0]["spends"][0]["comment"] == "line1\nline2"

        import json

        escaped = _escape_control_chars(text)
        assert json.loads(escaped)["categories"][0]["spends"][0]["comment"] == "line1\nline2"

        # pretty-printed JSON (structural newlines) with a raw newline in a value
        pretty = '{\n  "categories": [],\n  "notice": "a\nb"\n}'
        assert _parse_result(pretty)["notice"] == "a\nb"

        # model wraps the JSON with prose before/after
        wrapped = 'Вот результат:\n{"categories":[],"notice":"ok"}\nНадеюсь, помог!'
        assert _parse_result(wrapped)["notice"] == "ok"
