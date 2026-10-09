"""FastAPI Application for SUE Mapping, 811 Ticket Automation & ASCE 38-22 / 75-22 Compliance.

Optimized with Pydantic V2 Rust core serialization, global TypeAdapter instances,
TypedDict schemas, dynamic 18-24 inch tolerance zones, and automated CRS transformations.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app import crs
from app.clash import detect_clashes
from app.crs import STORAGE_SRID, from_storage, to_storage
from app.db import is_db_connected
from models.utility import (
    GEOJSON_FEATURE_COLLECTION_ADAPTER,
    UTILITY_FEATURE_ADAPTER,
    FeatureType,
    GeoJsonFeature,
    GeoJsonFeatureCollection,
    GeometryType,
    QualityLevel,
    UtilityFeature,
    UtilityFeatureModel,
)
from schemas.clash import (
    CLASH_DETECTION_RESULT_ADAPTER,
    ClashDetectionRequest,
)

logger = logging.getLogger(__name__)

# In-memory utility feature repository for fast demo & standalone execution
# Seeded with realistic Austin, TX SUE data in NAD83(2011) / Texas Central (EPSG:6578)
_IN_MEMORY_UTILITIES: list[UtilityFeature] = []


def _seed_sample_data() -> None:
    """Seed sample underground utilities in Austin, TX with ASCE 38-22 quality levels."""
    global _IN_MEMORY_UTILITIES
    if _IN_MEMORY_UTILITIES:
        return

    # Coordinates near downtown Austin, TX (~ -97.7431, 30.2672)
    # Stored in EPSG:4326 with 3D Z depths (meters below grade as negative values)
    samples: list[dict[str, Any]] = [
        {
            "id": "gas-main-01",
            "feature_type": FeatureType.SEGMENT.value,
            "quality_level": QualityLevel.QL_A.value,
            "horizontal_accuracy": 0.03,  # <= 0.06m required
            "vertical_accuracy": 0.02,  # <= 0.03m required
            "geometry_type": GeometryType.LINESTRING.value,
            "material": "Steel",
            "utility_type": "Natural Gas",
            "owner": "Texas Gas Service",
            "nominal_diameter_mm": 200.0,
            "source_srid": 6578,
            "geom": {
                "type": "LineString",
                "coordinates": [
                    [-97.7450, 30.2660, -1.5],
                    [-97.7420, 30.2680, -1.6],
                    [-97.7400, 30.2690, -1.5],
                ],
            },
        },
        {
            "id": "water-main-02",
            "feature_type": FeatureType.SEGMENT.value,
            "quality_level": QualityLevel.QL_B.value,
            "horizontal_accuracy": 0.05,  # <= 0.06m required
            "vertical_accuracy": 0.50,  # Unverified vertical
            "geometry_type": GeometryType.LINESTRING.value,
            "material": "Ductile Iron",
            "utility_type": "Potable Water",
            "owner": "Austin Water",
            "nominal_diameter_mm": 300.0,
            "source_srid": 6578,
            "geom": {
                "type": "LineString",
                "coordinates": [
                    [-97.7440, 30.2655, -2.1],
                    [-97.7425, 30.2675, -2.0],
                    [-97.7410, 30.2685, -2.2],
                ],
            },
        },
        {
            "id": "telecom-fiber-03",
            "feature_type": FeatureType.SEGMENT.value,
            "quality_level": QualityLevel.QL_C.value,
            "horizontal_accuracy": 0.30,
            "vertical_accuracy": 1.0,
            "geometry_type": GeometryType.LINESTRING.value,
            "material": "HDPE Conduit",
            "utility_type": "Telecommunications",
            "owner": "AT&T",
            "nominal_diameter_mm": 100.0,
            "source_srid": 4326,
            "geom": {
                "type": "LineString",
                "coordinates": [
                    [-97.7445, 30.2670, -0.9],
                    [-97.7430, 30.2672, -0.9],
                    [-97.7415, 30.2674, -0.9],
                ],
            },
        },
        {
            "id": "electric-vault-04",
            "feature_type": FeatureType.DEVICE.value,
            "quality_level": QualityLevel.QL_A.value,
            "horizontal_accuracy": 0.02,
            "vertical_accuracy": 0.01,
            "geometry_type": GeometryType.POINT.value,
            "material": "Precast Concrete",
            "utility_type": "Electric Power",
            "owner": "Austin Energy",
            "nominal_diameter_mm": 1200.0,
            "source_srid": 6578,
            "geom": {
                "type": "Point",
                "coordinates": [-97.7431, 30.2672, -2.5],
            },
        },
        {
            "id": "abandoned-clay-05",
            "feature_type": FeatureType.SEGMENT.value,
            "quality_level": QualityLevel.QL_D.value,
            "horizontal_accuracy": 1.5,
            "vertical_accuracy": 2.0,
            "geometry_type": GeometryType.LINESTRING.value,
            "material": "Vitrified Clay",
            "utility_type": "Sanitary Sewer",
            "owner": "City of Austin",
            "nominal_diameter_mm": 250.0,
            "source_srid": 4326,
            "geom": {
                "type": "LineString",
                "coordinates": [
                    [-97.7438, 30.2662, -3.2],
                    [-97.7428, 30.2678, -3.4],
                ],
            },
        },
    ]

    for s in samples:
        validated = UtilityFeatureModel.model_validate(s)
        _IN_MEMORY_UTILITIES.append(validated.to_typed_dict())


# Seed data immediately at module load
_seed_sample_data()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _seed_sample_data()
    yield


app = FastAPI(
    title="SUE Platform & 811 Ticket Automation API",
    description=(
        "ASCE 38-22 / 75-22 compliant subsurface utility mapping "
        "and 3D collision detection engine."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, Any]:
    """Health status check inspecting CRS engine and PostGIS database."""
    db_alive = is_db_connected()
    return {
        "status": "healthy",
        "service": "sue-platform",
        "postgis_connected": db_alive,
        "crs_engine": "pyproj",
        "storage_srid": STORAGE_SRID,
        "utilities_loaded": len(_IN_MEMORY_UTILITIES),
    }


@app.get(
    "/api/v1/utilities",
    tags=["Utilities"],
    response_class=Response,
)
def get_utilities(
    export_srid: int | None = Query(
        default=None,
        description="Optional NAD83 State Plane SRID to dynamically project coordinates for export",
    ),
) -> Response:
    """Return all utility assets as an ASCE 75-22 compliant GeoJSON FeatureCollection.

    Serialized at maximum speed using global TypeAdapter and Rust pydantic-core.
    """
    features: list[GeoJsonFeature] = []

    for u in _IN_MEMORY_UTILITIES:
        geom = u["geom"]
        if export_srid is not None and export_srid != STORAGE_SRID:
            geom = from_storage(geom, export_srid)

        features.append(
            {
                "type": "Feature",
                "id": u["id"],
                "geometry": geom,
                "properties": {
                    "id": u["id"],
                    "feature_type": u["feature_type"],
                    "quality_level": u["quality_level"],
                    "horizontal_accuracy": u["horizontal_accuracy"],
                    "vertical_accuracy": u["vertical_accuracy"],
                    "geometry_type": u["geometry_type"],
                    "material": u["material"],
                    "utility_type": u["utility_type"],
                    "owner": u.get("owner"),
                    "nominal_diameter_mm": u["nominal_diameter_mm"],
                    "source_srid": export_srid if export_srid else STORAGE_SRID,
                },
            }
        )

    fc: GeoJsonFeatureCollection = {
        "type": "FeatureCollection",
        "features": features,
    }

    # High-performance binary serialization with pydantic-core
    raw_json = GEOJSON_FEATURE_COLLECTION_ADAPTER.dump_json(fc)
    return Response(content=raw_json, media_type="application/geo+json")


@app.post(
    "/api/v1/utilities",
    tags=["Utilities"],
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
)
def create_utility(payload: dict[str, Any]) -> Response:
    """Ingest a new subsurface utility feature conforming strictly to ASCE 38-22 / 75-22.

    All incoming spatial coordinates are immediately transformed to EPSG:4326.
    """
    try:
        model = UtilityFeatureModel.model_validate(payload)
    except ValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=err.errors(),
        ) from err

    # Immediate coordinate transformation to WGS84 3D
    source_srid = model.source_srid
    stored_geom = to_storage(model.geom, source_srid)

    typed_feature: UtilityFeature = {
        "id": model.id or str(uuid4()),
        "feature_type": model.feature_type.value,  # type: ignore[typeddict-item]
        "quality_level": model.quality_level.value,  # type: ignore[typeddict-item]
        "horizontal_accuracy": model.horizontal_accuracy,
        "vertical_accuracy": model.vertical_accuracy,
        "geometry_type": model.geometry_type.value,  # type: ignore[typeddict-item]
        "material": model.material,
        "utility_type": model.utility_type,
        "owner": model.owner,
        "nominal_diameter_mm": model.nominal_diameter_mm,
        "source_srid": source_srid,
        "geom": stored_geom,  # type: ignore[typeddict-item]
    }

    _IN_MEMORY_UTILITIES.append(typed_feature)

    raw_json = UTILITY_FEATURE_ADAPTER.dump_json(typed_feature)
    return Response(
        content=raw_json,
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


@app.post(
    "/api/v1/clash-detect",
    tags=["Clash Detection"],
    response_class=Response,
)
def detect_clash_endpoint(request: ClashDetectionRequest) -> Response:
    """Perform real-time 3D clash detection for an 811 excavation ticket.

    Enforces dynamic statutory 18-to-24 inch tolerance zones emulating vacuum excavation.
    Returns high-performance serialized JSON results and GeoJSON features.
    """
    try:
        result = detect_clashes(
            ticket=request.ticket,
            utilities=_IN_MEMORY_UTILITIES,
            export_srid=request.export_srid,
        )
    except crs.CRSError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CRS transformation error: {err}",
        ) from err
    except Exception as err:
        logger.exception("Clash detection failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Clash detection error: {err}",
        ) from err

    raw_json = CLASH_DETECTION_RESULT_ADAPTER.dump_json(result)
    return Response(content=raw_json, media_type="application/json")
