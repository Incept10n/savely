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
