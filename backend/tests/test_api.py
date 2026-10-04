"""API contract tests that don't run the full pipeline."""

from fastapi.testclient import TestClient

from exovet.api.app import app


def test_health():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "components" in r.json()


def test_features_registry():
    client = TestClient(app)
    r = client.get("/api/features")
    body = r.json()
    assert len(body["scalar_order"]) == 33
    assert len(body["features"]) == 33
    for name in body["scalar_order"]:
        f = body["features"][name]
        assert f["definition"] and f["unit"] and f["source"]


def test_classes():
    client = TestClient(app)
    r = client.get("/api/classes")
    names = [c["name"] for c in r.json()["classes"]]
    assert names == [
        "planet_candidate", "eclipsing_binary", "grazing_eclipsing_binary",
        "contamination_blend", "instrumental_systematic",
    ]


def test_analyze_rejects_bad_input():
    client = TestClient(app)
    r = client.post("/api/analyze", json={"tic_id": -5})
    assert r.status_code == 400
