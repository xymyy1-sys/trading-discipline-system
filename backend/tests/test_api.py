def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

def test_market_sector_flow(client):
    response = client.get("/api/market/sector-flow")
    assert response.status_code == 200
    data = response.json()
    assert "source" in data
    assert "inflow" in data
    assert "outflow" in data
    assert isinstance(data["inflow"], list)
    assert isinstance(data["outflow"], list)
