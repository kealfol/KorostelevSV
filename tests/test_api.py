from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ITEMS_PER_PAGE", "20")
    from app.routers.items import fake_db
    from main import app

    fake_db.clear()
    fake_db[1] = {"name": "Foo", "price": 50.2}
    with TestClient(app) as test_client:
        yield test_client
    fake_db.clear()
    fake_db[1] = {"name": "Foo", "price": 50.2}


def test_root_and_settings(client):
    assert client.get("/").json() == {"message": "Hello, FastAPI!"}
    assert client.get("/info").json() == {
        "app_name": "My FastAPI Lab", "admin_email": "admin@example.com", "items_per_page": 20,
    }


def test_documentation(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    spec = client.get("/openapi.json").json()
    assert spec["info"]["version"] == "1.0.0"
    assert spec["info"]["contact"]["email"] == "admin@example.com"
    assert "put" in spec["paths"]["/items/{item_id}"]
    assert spec["components"]["schemas"]["Item"]["properties"]["price"]["exclusiveMinimum"] == 0


def test_read_item(client):
    assert client.get("/items/1?q=test").json() == {
        "item_id": 1, "item": {"name": "Foo", "price": 50.2}, "q": "test",
    }
    assert client.get("/items/1").json()["q"] is None


@pytest.mark.parametrize("path", ["/items/0", "/items/-1", "/items/text", "/items/1?q=" + "x" * 51])
def test_invalid_parameters(client, path):
    assert client.get(path).status_code == 422


def test_query_boundary(client):
    assert client.get("/items/1", params={"q": "x" * 50}).status_code == 200


def test_create_and_read(client):
    item = {"name": "Телефон", "price": 199.99, "is_offer": True}
    response = client.post("/items/", json=item)
    assert response.status_code == 200
    assert response.json() == item
    assert response.headers["location"] == "/items/2"
    assert client.get(response.headers["location"]).json()["item"] == item


def test_optional_offer_and_trimmed_name(client):
    response = client.post("/items/", json={"name": "  Телефон  ", "price": 10})
    assert response.json() == {"name": "Телефон", "price": 10.0, "is_offer": None}


@pytest.mark.parametrize("body", [
    {}, {"name": "Phone"}, {"price": 10}, {"name": "Phone", "price": 0},
    {"name": "Phone", "price": -1}, {"name": "Phone", "price": "NaN"},
    {"name": "Phone", "price": "Infinity"}, {"name": "Phone", "price": "abc"},
    {"name": "", "price": 10}, {"name": "   ", "price": 10},
])
def test_invalid_body(client, body):
    assert client.post("/items/", json=body).status_code == 422
    assert client.put("/items/1", json=body).status_code == 422
    assert client.get("/items/1").json()["item"]["name"] == "Foo"


@pytest.mark.parametrize("method", ["get", "put"])
def test_missing_item(client, method):
    kwargs = {"json": {"name": "New", "price": 10}} if method == "put" else {}
    response = getattr(client, method)("/items/999", **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Item not found"}


def test_update_and_read(client):
    item = {"name": "Обновленный товар", "price": 75, "is_offer": False}
    response = client.put("/items/1", json=item)
    assert response.status_code == 200
    assert response.json() == item
    assert client.get("/items/1").json()["item"] == item


def test_update_same_name_is_allowed(client):
    assert client.put("/items/1", json={"name": "Foo", "price": 80}).status_code == 200


@pytest.mark.parametrize("name", ["Foo", "foo", "  FOO  "])
def test_duplicate_names(client, name):
    response = client.post("/items/", json={"name": name, "price": 10})
    assert response.status_code == 409
    assert response.json() == {"detail": "Item name already exists"}
    client.post("/items/", json={"name": "Bar", "price": 20})
    response = client.put("/items/2", json={"name": name, "price": 30})
    assert response.status_code == 409
    assert client.get("/items/2").json()["item"]["name"] == "Bar"


def test_repeated_post_and_renamed_item(client):
    body = {"name": "Bar", "price": 20}
    assert client.post("/items/", json=body).status_code == 200
    assert client.post("/items/", json=body).status_code == 409
    assert client.put("/items/2", json={"name": "Baz", "price": 25}).status_code == 200
    assert client.post("/items/", json=body).status_code == 200


def test_concurrent_duplicate_creation(client):
    def create(_):
        return client.post("/items/", json={"name": "Same", "price": 10}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == [200, 409]


def test_settings_file_and_environment(tmp_path, monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ITEMS_PER_PAGE", raising=False)
    env = tmp_path / ".env"
    env.write_text("ADMIN_EMAIL=file@example.com\nITEMS_PER_PAGE=20\n", encoding="utf-8")
    assert Settings(_env_file=env).items_per_page == 20
    monkeypatch.setenv("ITEMS_PER_PAGE", "30")
    assert Settings(_env_file=env).items_per_page == 30
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_request_logging(client, caplog):
    with caplog.at_level("INFO", logger="lab6"):
        client.get("/items/1", params={"q": "private-query-value"})
        client.get("/items/999")
        client.post("/items/", json={"name": "Foo", "price": 10})
        client.post("/items/", json={"name": "New", "price": -1})
    assert "GET /items/1 -> 200" in caplog.text
    assert "GET /items/999 -> 404" in caplog.text
    assert "POST /items/ -> 409" in caplog.text
    assert "POST /items/ -> 422" in caplog.text
    assert "private-query-value" not in caplog.text


def test_item_operation_logging(client, caplog):
    with caplog.at_level("INFO", logger="lab6"):
        client.post("/items/", json={"name": "New", "price": 10})
        client.put("/items/2", json={"name": "Updated", "price": 15})
    assert "Item created: id=2" in caplog.text
    assert "Item updated: id=2" in caplog.text
