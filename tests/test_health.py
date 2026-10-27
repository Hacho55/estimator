def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["version"] == "0.1.0"
    assert body["model"] == "openrouter/openai/gpt-4o-mini"


def test_local_front_origin_can_call_the_api(client):
    for origin in ("http://127.0.0.1:3000", "http://localhost:3001"):
        response = client.options(
            "/api/v1/estimate/stream",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert "DELETE" in response.headers["access-control-allow-methods"]
