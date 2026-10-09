"""Tests for 3D Collision Detection and Dynamic 18-24 inch Tolerance Zones."""

import pytest

from app.clash import (
    build_postgis_clash_query,
    detect_clashes,
    get_utm_epsg_for_lon_lat,
)
from models.utility import UtilityFeature
from schemas.clash import (
    TicketInputModel,
    get_tolerance_m,
)


def test_statutory_tolerance_zones():
    # Texas: 18 inches = 0.4572m
    tx_tol = get_tolerance_m("TX")
    assert tx_tol == pytest.approx(18.0 * 0.0254, abs=1e-5)

    # California: 24 inches = 0.6096m
    ca_tol = get_tolerance_m("CA")
    assert ca_tol == pytest.approx(24.0 * 0.0254, abs=1e-5)

    # Default conservative within range
    def_tol = get_tolerance_m("UNKNOWN")
    assert def_tol == pytest.approx(24.0 * 0.0254, abs=1e-5)

    # Valid override within 18-24 in
    override = get_tolerance_m("TX", override_in=20.0)
    assert override == pytest.approx(20.0 * 0.0254, abs=1e-5)

    # Invalid overrides outside statutory range raise ValueError
    with pytest.raises(ValueError, match="between 18 and 24"):
        get_tolerance_m("TX", override_in=12.0)

    with pytest.raises(ValueError, match="between 18 and 24"):
        get_tolerance_m("TX", override_in=30.0)


def test_utm_zone_selection():
    # Austin, TX (-97.74, 30.27) -> UTM Zone 14N (EPSG:32614)
    epsg = get_utm_epsg_for_lon_lat(-97.74, 30.27)
    assert epsg == 32614


def test_postgis_clash_query_builder():
    sql = build_postgis_clash_query(
        ticket_id="TICK-1234",
        metric_srid=32614,
        tolerance_m=0.4572,
        excavation_depth_m=3.0,
    )
    assert "ST_3DDWithin" in sql
    assert "ST_Extrude" in sql
    assert "ST_Expand" in sql
    assert "utility_features" in sql


def test_3d_clash_evaluation_scenarios():
    # Dig ticket centered around (-97.7430, 30.2670), 2m depth below grade (0 to -2m)
    ticket_poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [-97.7440, 30.2660],
                [-97.7420, 30.2660],
                [-97.7420, 30.2680],
                [-97.7440, 30.2680],
                [-97.7440, 30.2660],
            ]
        ],
    }

    ticket = TicketInputModel(
        ticket_id="TICK-TEST-1",
        state_code="TX",  # 18 in tolerance
        polygon=ticket_poly,
        ground_elevation_m=0.0,
        excavation_depth_m=2.0,
    )

    utilities: list[UtilityFeature] = [
        # 1. QL-A utility directly intersecting horizontal footprint and inside depth (-1.0m)
        # Result -> COLLISION
        {
            "id": "u-collision",
            "feature_type": "Segment",
            "quality_level": "QL-A",
            "horizontal_accuracy": 0.02,
            "vertical_accuracy": 0.01,
            "geometry_type": "LineString",
            "material": "Steel",
            "utility_type": "Gas",
            "owner": "Gas Corp",
            "nominal_diameter_mm": 100.0,
            "source_srid": 4326,
            "geom": {
                "type": "LineString",
                "coordinates": [
                    [-97.7435, 30.2665, -1.0],
                    [-97.7425, 30.2675, -1.0],
                ],
            },
        },
        # 2. QL-A utility directly under excavation footprint horizontally,
        # but deep below grade (-8.0m). Because it is QL-A (verified Z), it is CLEAR!
        {
            "id": "u-deep-qla",
            "feature_type": "Segment",
            "quality_level": "QL-A",
            "horizontal_accuracy": 0.02,
            "vertical_accuracy": 0.02,
            "geometry_type": "LineString",
            "material": "Ductile Iron",
            "utility_type": "Water",
            "owner": "Water Corp",
            "nominal_diameter_mm": 200.0,
            "source_srid": 4326,
            "geom": {
                "type": "LineString",
                "coordinates": [
                    [-97.7435, 30.2665, -8.0],
                    [-97.7425, 30.2675, -8.0],
                ],
            },
        },
        # 3. QL-B utility directly under footprint horizontally, claiming -8.0m depth
        # Because it is QL-B (vertical unverified under ASCE 38-22), it CANNOT be cleared by depth!
        # It must be flagged as COLLISION / requiring vacuum exposure (potholing)
        {
            "id": "u-deep-qlb",
            "feature_type": "Segment",
            "quality_level": "QL-B",
            "horizontal_accuracy": 0.05,
            "vertical_accuracy": 1.0,
            "geometry_type": "LineString",
            "material": "PVC",
            "utility_type": "Telecom",
            "owner": "Telco",
            "nominal_diameter_mm": 50.0,
            "source_srid": 4326,
            "geom": {
                "type": "LineString",
                "coordinates": [
                    [-97.7435, 30.2665, -8.0],
                    [-97.7425, 30.2675, -8.0],
                ],
            },
        },
        # 4. Faraway utility (> 500m away) -> CLEAR
        {
            "id": "u-faraway",
            "feature_type": "Device",
            "quality_level": "QL-C",
            "horizontal_accuracy": 0.20,
            "vertical_accuracy": 0.50,
            "geometry_type": "Point",
            "material": "Concrete",
            "utility_type": "Electric",
            "owner": "Power Co",
            "nominal_diameter_mm": 0.0,
            "source_srid": 4326,
            "geom": {
                "type": "Point",
                "coordinates": [-97.7300, 30.2500, -1.0],
            },
        },
    ]

    res = detect_clashes(ticket=ticket, utilities=utilities)
    summary = res["summary"]
    clashes = {c["utility_id"]: c for c in res["clashes"]}

    assert summary["total_utilities_evaluated"] == 4
    assert summary["total_clashes"] == 2  # u-collision and u-deep-qlb

    assert clashes["u-collision"]["status"] == "COLLISION"
    assert clashes["u-deep-qla"]["status"] == "CLEAR"
    assert clashes["u-deep-qlb"]["status"] == "COLLISION"
    assert "vacuum exposure" in clashes["u-deep-qlb"]["mitigation_recommendation"]
    assert clashes["u-faraway"]["status"] == "CLEAR"
