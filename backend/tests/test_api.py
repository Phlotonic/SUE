"""Integration and unit tests for FastAPI REST endpoints."""


import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["storage_srid"] == 4326
        assert data["utilities_loaded"] >= 5


@pytest.mark.asyncio
async def test_get_utilities_geojson():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/utilities")
        assert response.status_code == 200
        assert "application/geo+json" in response.headers["content-type"]
        fc = response.json()
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) >= 5

        # Check properties of first feature
        f = fc["features"][0]
        assert f["type"] == "Feature"
        assert "quality_level" in f["properties"]
        assert "material" in f["properties"]
        assert "horizontal_accuracy" in f["properties"]
        assert "vertical_accuracy" in f["properties"]


@pytest.mark.asyncio
async def test_get_utilities_with_state_plane_export():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Export to NAD83(2011) / Texas Central (ftUS) EPSG:6578
        response = await client.get("/api/v1/utilities?export_srid=6578")
        assert response.status_code == 200
        fc = response.json()
        assert len(fc["features"]) >= 5

        first_coords = fc["features"][0]["geometry"]["coordinates"][0]
        # In Texas Central ftUS, X is ~3,000,000+ ftUS
        assert first_coords[0] > 1_000_000


@pytest.mark.asyncio
async def test_create_utility_and_auto_transform():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "feature_type": "Segment",
            "quality_level": "QL-A",
            "horizontal_accuracy": 0.04,
            "vertical_accuracy": 0.02,
            "geometry_type": "LineString",
            "material": "HDPE",
            "utility_type": "Telecom",
            "owner": "Google Fiber",
            "nominal_diameter_mm": 50.0,
            "source_srid": 6578,  # Input in Texas Central ftUS!
            "geom": {
                "type": "LineString",
                "coordinates": [
                    [3115000.0, 10070000.0, -5.0],
                    [3115100.0, 10070050.0, -5.0],
                ],
            },
        }

        response = await client.post("/api/v1/utilities", json=payload)
        assert response.status_code == 201
        created = response.json()
        assert created["quality_level"] == "QL-A"
        # Stored in EPSG:4326 automatically! Longitude near -97.x, latitude near 30.x
        assert -98.0 < created["geom"]["coordinates"][0][0] < -97.0
        assert 30.0 < created["geom"]["coordinates"][0][1] < 31.0


@pytest.mark.asyncio
async def test_clash_detection_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ticket_req = {
            "ticket": {
                "ticket_id": "TICK-AUSTIN-1",
                "state_code": "TX",
                "polygon": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-97.744, 30.266],
                            [-97.741, 30.266],
                            [-97.741, 30.269],
                            [-97.744, 30.269],
                            [-97.744, 30.266],
                        ]
                    ],
                },
                "ground_elevation_m": 0.0,
                "excavation_depth_m": 2.5,
                "work_type": "Water Main Repair",
                "excavator": "Austin Trenching Co.",
            }
        }

        response = await client.post("/api/v1/clash-detect", json=ticket_req)
        assert response.status_code == 200
        data = response.json()

        assert "summary" in data
        assert "clashes" in data
        assert "geojson" in data
        assert data["summary"]["ticket_id"] == "TICK-AUSTIN-1"
        assert data["summary"]["statutory_tolerance_in"] == 18.0
        assert data["summary"]["total_clashes"] >= 1
