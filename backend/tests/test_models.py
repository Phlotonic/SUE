"""Tests for ASCE 38-22 and ASCE 75-22 data models and validation constraints."""

import pytest
from pydantic import ValidationError

from models.utility import (
    QualityLevel,
    UtilityFeatureModel,
)


def test_valid_ql_a_utility():
    data = {
        "feature_type": "Segment",
        "quality_level": "QL-A",
        "horizontal_accuracy": 0.05,  # <= 0.06m
        "vertical_accuracy": 0.02,  # <= 0.03m
        "geometry_type": "LineString",
        "material": "Ductile Iron",
        "utility_type": "Water",
        "geom": {
            "type": "LineString",
            "coordinates": [[-97.74, 30.26, -1.5], [-97.73, 30.27, -1.5]],
        },
    }
    model = UtilityFeatureModel.model_validate(data)
    assert model.quality_level == QualityLevel.QL_A
    assert model.horizontal_accuracy == 0.05
    assert model.vertical_accuracy == 0.02
    assert model.postgis_datatype == "Geometry(LineStringZ, 4326)"


def test_ql_a_violating_horizontal_accuracy_fails():
    data = {
        "feature_type": "Segment",
        "quality_level": "QL-A",
        "horizontal_accuracy": 0.08,  # > 0.06m violation
        "vertical_accuracy": 0.02,
        "geometry_type": "LineString",
        "material": "Steel",
        "utility_type": "Gas",
        "geom": {
            "type": "LineString",
            "coordinates": [[-97.74, 30.26, -1.0], [-97.73, 30.27, -1.0]],
        },
    }
    with pytest.raises(ValidationError, match="horizontal_accuracy <= 0.06m"):
        UtilityFeatureModel.model_validate(data)


def test_ql_a_violating_vertical_accuracy_fails():
    data = {
        "feature_type": "Segment",
        "quality_level": "QL-A",
        "horizontal_accuracy": 0.04,
        "vertical_accuracy": 0.05,  # > 0.03m violation
        "geometry_type": "LineString",
        "material": "Steel",
        "utility_type": "Gas",
        "geom": {
            "type": "LineString",
            "coordinates": [[-97.74, 30.26, -1.0], [-97.73, 30.27, -1.0]],
        },
    }
    with pytest.raises(ValidationError, match="vertical_accuracy <= 0.03m"):
        UtilityFeatureModel.model_validate(data)


def test_ql_b_accuracy_constraints():
    # QL-B requires horizontal <= 0.06m, but vertical can be larger
    valid_ql_b = {
        "feature_type": "Segment",
        "quality_level": "QL-B",
        "horizontal_accuracy": 0.06,
        "vertical_accuracy": 0.50,  # Unverified vertical is acceptable for QL-B
        "geometry_type": "LineString",
        "material": "PVC",
        "utility_type": "Sewer",
        "geom": {
            "type": "LineString",
            "coordinates": [[-97.74, 30.26, -2.0], [-97.73, 30.27, -2.0]],
        },
    }
    model = UtilityFeatureModel.model_validate(valid_ql_b)
    assert model.quality_level == QualityLevel.QL_B

    # QL-B with horizontal > 0.06m fails
    invalid_ql_b = {**valid_ql_b, "horizontal_accuracy": 0.10}
    with pytest.raises(ValidationError, match="horizontal_accuracy <= 0.06m"):
        UtilityFeatureModel.model_validate(invalid_ql_b)


def test_geometry_types_postgis_mapping():
    base = {
        "feature_type": "Device",
        "quality_level": "QL-C",
        "horizontal_accuracy": 0.20,
        "vertical_accuracy": 0.50,
        "material": "Concrete",
        "utility_type": "Electric",
        "geom": {"type": "Point", "coordinates": [-97.74, 30.26, -1.0]},
    }

    pt = UtilityFeatureModel.model_validate({**base, "geometry_type": "Point"})
    assert pt.postgis_datatype == "Geometry(PointZ, 4326)"

    ls = UtilityFeatureModel.model_validate(
        {
            **base,
            "geometry_type": "LineString",
            "geom": {
                "type": "LineString",
                "coordinates": [[-97.74, 30.26, -1.0], [-97.73, 30.27, -1.0]],
            },
        }
    )
    assert ls.postgis_datatype == "Geometry(LineStringZ, 4326)"

    poly = UtilityFeatureModel.model_validate(
        {
            **base,
            "geometry_type": "Polygon",
            "geom": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-97.74, 30.26, 0.0],
                        [-97.73, 30.26, 0.0],
                        [-97.73, 30.27, 0.0],
                        [-97.74, 30.27, 0.0],
                        [-97.74, 30.26, 0.0],
                    ]
                ],
            },
        }
    )
    assert poly.postgis_datatype == "Geometry(PolygonZ, 4326)"

    obj3d = UtilityFeatureModel.model_validate(
        {
            **base,
            "geometry_type": "3DObject",
            "geom": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-97.74, 30.26, -2.0],
                        [-97.73, 30.26, -2.0],
                        [-97.73, 30.27, -2.0],
                        [-97.74, 30.27, -2.0],
                        [-97.74, 30.26, -2.0],
                    ]
                ],
            },
        }
    )
    assert obj3d.postgis_datatype == "Geometry(PolyhedralSurfaceZ, 4326)"
