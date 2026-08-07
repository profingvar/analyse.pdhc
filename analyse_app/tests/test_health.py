"""Health probe smoke tests: canonical §10 shape at both paths, no-auth."""


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["service"] == "analyse.pdhc"
    assert "version" in body


def test_api_v1_health_alias(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.get_json()["service"] == "analyse.pdhc"


def test_health_no_auth_required(client):
    # Both health paths must be reachable without any Authorization header.
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
