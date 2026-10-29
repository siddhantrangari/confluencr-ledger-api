import uuid

from tests.helpers import create_account


def test_create_account_returns_system_generated_fields(client):
    res = client.post("/accounts", json={"name": "Alice"})
    assert res.status_code == 201
    body = res.json()
    assert set(body) == {"id", "name", "balance", "created_at"}
    uuid.UUID(body["id"])
    assert body["name"] == "Alice"
    assert body["balance"] == "0.00"
    assert body["created_at"].endswith("Z") or "+00:00" in body["created_at"]


def test_account_ids_are_unique(client):
    ids = {create_account(client, f"user{i}") for i in range(5)}
    assert len(ids) == 5


def test_name_is_trimmed(client):
    assert client.post("/accounts", json={"name": "  Bob  "}).json()["name"] == "Bob"


def test_client_cannot_set_initial_balance(client):
    res = client.post("/accounts", json={"name": "Alice", "balance": 500})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert res.json()["error"]["details"][0]["field"] == "balance"


def test_client_cannot_set_id_or_created_at(client):
    for extra in ({"id": str(uuid.uuid4())}, {"created_at": "2020-01-01T00:00:00Z"}):
        res = client.post("/accounts", json={"name": "Alice", **extra})
        assert res.status_code == 400


def test_name_is_required(client):
    for body in ({}, {"name": ""}, {"name": "   "}, {"name": None}, {"name": 123}):
        res = client.post("/accounts", json=body)
        assert res.status_code == 400, body
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_name_too_long(client):
    assert client.post("/accounts", json={"name": "x" * 101}).status_code == 400


def test_malformed_json_body(client):
    res = client.post("/accounts", content=b"{not json", headers={"Content-Type": "application/json"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_REQUEST_BODY"
    assert res.json()["error"]["details"] == [{"field": "body", "message": "Request body must be a valid JSON object"}]


def test_body_must_be_an_object(client):
    res = client.post("/accounts", json=["Alice"])
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_REQUEST_BODY"


def test_get_account_and_balance(client):
    account_id = create_account(client)
    assert client.get(f"/accounts/{account_id}").json()["name"] == "Alice"
    res = client.get(f"/accounts/{account_id}/balance")
    assert res.status_code == 200
    assert res.json() == {"account_id": account_id, "balance": "0.00"}


def test_balance_of_unknown_account_is_404(client):
    missing = str(uuid.uuid4())
    res = client.get(f"/accounts/{missing}/balance")
    assert res.status_code == 404
    error = res.json()["error"]
    assert error["code"] == "ACCOUNT_NOT_FOUND"
    assert error["details"] == {"account_id": missing}


def test_malformed_account_id_is_400(client):
    res = client.get("/accounts/not-a-uuid/balance")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_error_shape_is_consistent_and_has_request_id(client):
    res = client.get(f"/accounts/{uuid.uuid4()}", headers={"X-Request-ID": "trace-123"})
    assert set(res.json()["error"]) == {"code", "message", "details", "request_id"}
    assert res.json()["error"]["request_id"] == "trace-123"
    assert res.headers["X-Request-ID"] == "trace-123"


def test_unknown_route_uses_same_error_shape(client):
    res = client.get("/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_openapi_documents_400_errors_not_framework_422(client):
    spec = client.get("/openapi.json").json()
    assert "HTTPValidationError" not in str(spec)
    create = spec["paths"]["/accounts"]["post"]["responses"]
    assert "400" in create and "422" not in create
