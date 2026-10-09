"""ASCE 38-22 and ASCE 75-22 Subsurface Utility Infrastructure Data Models.

Mandatory Data Attributes:
- quality_level: Enum (QL-A, QL-B, QL-C, QL-D) mapping to ASCE 38-22 definitions.
- horizontal_accuracy: Float (in meters). QL-B requires <= 0.06m (0.2 ft).
- vertical_accuracy: Float (in meters). QL-A requires <= 0.03m (0.1 ft).
- geometry_type: Enum (Point, LineString, Polygon, 3DObject).
- material: String (e.g., PVC, Ductile Iron, HDPE).

PostGIS 3D datatypes explicitly defined:
- Geometry(PointZ, 4326)
- Geometry(LineStringZ, 4326)
- Geometry(PolygonZ, 4326)
- Geometry(PolyhedralSurfaceZ, 4326)

All TypeAdapter objects are instantiated globally at module load to avoid
per-request constructor overhead and leverage pydantic-core Rust serialization.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)
from typing_extensions import TypedDict

# Explicit PostGIS 3D Spatial Datatypes
POSTGIS_POINTZ: str = "Geometry(PointZ, 4326)"
POSTGIS_LINESTRINGZ: str = "Geometry(LineStringZ, 4326)"
POSTGIS_POLYGONZ: str = "Geometry(PolygonZ, 4326)"
POSTGIS_3DOBJECTZ: str = "Geometry(PolyhedralSurfaceZ, 4326)"


class QualityLevel(StrEnum):
    """ASCE 38-22 Quality Levels."""

    QL_A = "QL-A"  # Precise 3D position exposed via vacuum excavation
    QL_B = "QL-B"  # Surface geophysics (horizontal only)
    QL_C = "QL-C"  # Surveyed surface appurtenances + records correlation
    QL_D = "QL-D"  # Existing utility records / oral history


class GeometryType(StrEnum):
    """ASCE 75-22 Supported Geometry Types."""

    POINT = "Point"
    LINESTRING = "LineString"
    POLYGON = "Polygon"
    OBJECT_3D = "3DObject"


class FeatureType(StrEnum):
    """Utility feature classification."""

    SEGMENT = "Segment"
    DEVICE = "Device"
    ACCESS_POINT = "AccessPoint"


# TypedDict representations for maximum serialization throughput
class GeoJSONGeometry(TypedDict):
    type: Literal[
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
    ]
    coordinates: Any


class UtilityFeature(TypedDict):
    """High-performance TypedDict representation conforming to ASCE 75-22."""

    id: str
    feature_type: Literal["Segment", "Device", "AccessPoint"]
    quality_level: Literal["QL-A", "QL-B", "QL-C", "QL-D"]
    horizontal_accuracy: float
    vertical_accuracy: float
    geometry_type: Literal["Point", "LineString", "Polygon", "3DObject"]
    material: str
    utility_type: str
    owner: str | None
    nominal_diameter_mm: float
    source_srid: int
    geom: GeoJSONGeometry


class GeoJsonFeatureProperties(TypedDict, total=False):
    id: str
    feature_type: str
    quality_level: str
    horizontal_accuracy: float
    vertical_accuracy: float
    geometry_type: str
    material: str
    utility_type: str
    owner: str | None
    nominal_diameter_mm: float
    source_srid: int
    tolerance_zone_m: float
    is_clash: bool
    clash_reason: str | None


class GeoJsonFeature(TypedDict):
    type: Literal["Feature"]
    id: str | None
    geometry: GeoJSONGeometry
    properties: GeoJsonFeatureProperties


class GeoJsonFeatureCollection(TypedDict):
    type: Literal["FeatureCollection"]
    features: list[GeoJsonFeature]


class UtilityFeatureModel(BaseModel):
    """Pydantic V2 Model enforcing ASCE 38-22 and 75-22 validation rules.

    Employs model_validator(mode='after') to enforce quality level tolerances.
    Wrap validators are strictly prohibited.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    id: str = Field(default_factory=lambda: str(uuid4()))
    feature_type: FeatureType = Field(
        ...,
        description="Utility classification (Segment, Device, AccessPoint)",
    )
    quality_level: QualityLevel = Field(
        ...,
        description="ASCE 38-22 Quality Level (QL-A, QL-B, QL-C, QL-D)",
    )
    horizontal_accuracy: float = Field(
        ...,
        gt=0.0,
        le=30.0,
        description="Horizontal positioning accuracy in meters (e.g. <=0.06m for QL-B)",
    )
    vertical_accuracy: float = Field(
        ...,
        gt=0.0,
        le=30.0,
        description="Vertical positioning accuracy in meters (e.g. <=0.03m for QL-A)",
    )
    geometry_type: GeometryType = Field(
        ...,
        description="ASCE 75-22 Geometry Type (Point, LineString, Polygon, 3DObject)",
    )
    material: str = Field(
        ...,
        min_length=1,
        description="Asset material (e.g. PVC, Ductile Iron, HDPE, Steel)",
    )
    utility_type: str = Field(
        ...,
        min_length=1,
        description="Service type (e.g. Water, Gas, Electric, Telecom, Wastewater)",
    )
    owner: str | None = Field(default=None, description="Utility owner or operating agency")
    nominal_diameter_mm: float = Field(
        default=0.0,
        ge=0.0,
        le=5000.0,
        description="Nominal pipe diameter in millimeters",
    )
    source_srid: int = Field(
        default=4326,
        description="Original source coordinate reference system SRID",
    )
    geom: dict[str, Any] = Field(
        ...,
        description="3D GeoJSON geometry in EPSG:4326",
    )

    @property
    def postgis_datatype(self) -> str:
        """Return the matching explicit PostGIS 3D spatial datatype."""
        match self.geometry_type:
            case GeometryType.POINT:
                return POSTGIS_POINTZ
            case GeometryType.LINESTRING:
                return POSTGIS_LINESTRINGZ
            case GeometryType.POLYGON:
                return POSTGIS_POLYGONZ
            case GeometryType.OBJECT_3D:
                return POSTGIS_3DOBJECTZ

    @model_validator(mode="after")
    def validate_asce_accuracy_thresholds(self) -> UtilityFeatureModel:
        """Validate ASCE 38-22 quality level accuracy standards without wrap validators."""
        if self.quality_level == QualityLevel.QL_A:
            if self.horizontal_accuracy > 0.06:
                raise ValueError(
                    f"ASCE 38-22 QL-A requires horizontal_accuracy <= 0.06m "
                    f"(got {self.horizontal_accuracy}m)"
                )
            if self.vertical_accuracy > 0.03:
                raise ValueError(
                    f"ASCE 38-22 QL-A requires vertical_accuracy <= 0.03m "
                    f"(got {self.vertical_accuracy}m)"
                )
        elif self.quality_level == QualityLevel.QL_B:
            if self.horizontal_accuracy > 0.06:
                raise ValueError(
                    f"ASCE 38-22 QL-B requires horizontal_accuracy <= 0.06m "
                    f"(got {self.horizontal_accuracy}m)"
                )
        return self

    def to_typed_dict(self) -> UtilityFeature:
        """Convert validated model to high-performance TypedDict."""
        return {
            "id": self.id,
            "feature_type": self.feature_type.value,  # type: ignore[typeddict-item]
            "quality_level": self.quality_level.value,  # type: ignore[typeddict-item]
            "horizontal_accuracy": self.horizontal_accuracy,
            "vertical_accuracy": self.vertical_accuracy,
            "geometry_type": self.geometry_type.value,  # type: ignore[typeddict-item]
            "material": self.material,
            "utility_type": self.utility_type,
            "owner": self.owner,
            "nominal_diameter_mm": self.nominal_diameter_mm,
            "source_srid": self.source_srid,
            "geom": self.geom,  # type: ignore[typeddict-item]
        }


# ==============================================================================
# Global TypeAdapter instances initialized once at module load (Rust Core)
# ==============================================================================
UTILITY_FEATURE_ADAPTER: TypeAdapter[UtilityFeature] = TypeAdapter(UtilityFeature)
UTILITY_FEATURE_LIST_ADAPTER: TypeAdapter[list[UtilityFeature]] = TypeAdapter(list[UtilityFeature])
GEOJSON_FEATURE_ADAPTER: TypeAdapter[GeoJsonFeature] = TypeAdapter(GeoJsonFeature)
GEOJSON_FEATURE_COLLECTION_ADAPTER: TypeAdapter[GeoJsonFeatureCollection] = TypeAdapter(
    GeoJsonFeatureCollection
)
