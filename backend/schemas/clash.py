"""811 Ticket and 3D Collision Detection Schemas.

Enforces dynamic 18-to-24 inch tolerance zones emulating physical vacuum excavation
clearances and ASCE 38-22 quality level reliability.

TypeAdapter instances are initialized globally at module load to leverage
pydantic-core Rust deserialization and serialization.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
)
from typing_extensions import TypedDict

from models.utility import (
    GeoJsonFeatureCollection,
)

# Statutory 18-to-24 inch tolerance zones mapped by state jurisdiction
# Converts inches to meters: 1 inch = 0.0254 meters
INCHES_TO_METERS: float = 0.0254

STATE_TOLERANCE_INCHES: dict[str, float] = {
    "TX": 18.0,  # Tex. Util. Code ch. 251 (18 inches + 1/2 pipe diameter)
    "OH": 18.0,  # Ohio Rev. Code 3781.25
    "MD": 18.0,  # Md. Public Utilities 12-101
    "CA": 24.0,  # Cal. Gov. Code 4216 (24 inches)
    "FL": 24.0,  # Fla. Stat. ch. 556
    "IN": 24.0,  # Ind. Code 8-1-26
    "NY": 24.0,  # 16 NYCRR Part 753
    "NC": 24.0,  # N.C.G.S. 87-115
    "OK": 24.0,  # Okla. Stat. tit. 63
    "SC": 24.0,  # S.C. Code 58-36
}
DEFAULT_TOLERANCE_INCHES: float = 24.0  # Conservative national default within 18-24 in


def get_tolerance_m(state_code: str, override_in: float | None = None) -> float:
    """Return the tolerance zone buffer distance in meters.

    Enforces invariant: tolerance zone must be between 18.0 and 24.0 inches.
    """
    if override_in is not None:
        if not (18.0 <= override_in <= 24.0):
            raise ValueError(
                f"Tolerance override must be between 18 and 24 inches (got {override_in})"
            )
        return override_in * INCHES_TO_METERS

    inches = STATE_TOLERANCE_INCHES.get(state_code.upper(), DEFAULT_TOLERANCE_INCHES)
    return inches * INCHES_TO_METERS


# TypedDict response types for zero-overhead JSON serialization
class ClashDetail(TypedDict):
    utility_id: str
    feature_type: str
    quality_level: str
    geometry_type: str
    material: str
    utility_type: str
    horizontal_accuracy_m: float
    vertical_accuracy_m: float
    tolerance_zone_m: float
    nominal_diameter_mm: float
    calculated_3d_clearance_m: float
    status: Literal["COLLISION", "WITHIN_TOLERANCE_ZONE", "CLEAR"]
    mitigation_recommendation: str


class ClashSummary(TypedDict):
    ticket_id: str
    total_utilities_evaluated: int
    total_clashes: int
    quality_breakdown: dict[str, int]
    statutory_tolerance_in: float
    tolerance_buffer_m: float


class ClashDetectionResult(TypedDict):
    summary: ClashSummary
    clashes: list[ClashDetail]
    geojson: GeoJsonFeatureCollection


class TicketInputModel(BaseModel):
    """811 Dig Ticket Submission Model."""

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    ticket_id: str = Field(default_factory=lambda: f"TICK-{uuid4().hex[:8].upper()}")
    state_code: str = Field(
        ...,
        min_length=2,
        max_length=2,
        description="Two-letter US State abbreviation (e.g. TX, CA)",
    )
    polygon: dict[str, Any] = Field(
        ...,
        description="2D or 3D GeoJSON Polygon / MultiPolygon defining excavation limits",
    )
    source_srid: int = Field(
        default=4326,
        description="Source Coordinate System SRID of ticket geometry (auto-transformed to 4326)",
    )
    ground_elevation_m: float = Field(
        default=0.0,
        description="Reference ground surface elevation in meters",
    )
    excavation_depth_m: float = Field(
        default=3.0,
        gt=0.0,
        le=100.0,
        description="Proposed maximum excavation depth in meters below grade (default 3m ~ 10ft)",
    )
    tolerance_in_override: float | None = Field(
        default=None,
        ge=18.0,
        le=24.0,
        description=(
            "Optional tolerance zone override strictly bounded between 18.0 and 24.0 inches"
        ),
    )
    work_type: str | None = Field(default="Excavation", description="Scope of planned work")
    excavator: str | None = Field(default=None, description="Contractor / excavator company")

    @field_validator("state_code")
    @classmethod
    def validate_state(cls, v: str) -> str:
        code = v.upper()
        if not code.isalpha():
            raise ValueError("State code must be two alphabetic characters")
        return code


class ClashDetectionRequest(BaseModel):
    """Request payload for real-time 3D clash detection."""

    ticket: TicketInputModel
    export_srid: int | None = Field(
        default=None,
        description="Optional NAD83 State Plane SRID to project output GeoJSON onto",
    )


# ==============================================================================
# Global TypeAdapter instances initialized once at module load (Rust Core)
# ==============================================================================
CLASH_DETAIL_ADAPTER: TypeAdapter[ClashDetail] = TypeAdapter(ClashDetail)
CLASH_SUMMARY_ADAPTER: TypeAdapter[ClashSummary] = TypeAdapter(ClashSummary)
CLASH_DETECTION_RESULT_ADAPTER: TypeAdapter[ClashDetectionResult] = TypeAdapter(
    ClashDetectionResult
)
TICKET_INPUT_ADAPTER: TypeAdapter[TicketInputModel] = TypeAdapter(TicketInputModel)
