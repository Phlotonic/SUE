"""3D Collision Detection & 811 Tolerance Zone Clearance Engine.

Implements statutory 18-to-24 inch dynamic tolerance zones emulating physical vacuum
excavation clearances and ASCE 38-22 / ASCE 75-22 quality level standards.

Key Mathematical Invariants:
-----------------------------
1. Storage is EPSG:4326 (WGS84 3D, Z in meters).
2. Distance computations must operate in metric projection units (meters), NEVER degrees.
3. R-Tree-over-GiST bounding box pre-filter utilizes 2D degree margins to leverage
   the spatial index before full 3D computation.
4. Vertical Reliability:
   - QL-A features have verified surveyed vertical depths (non-destructive vacuum exposure).
     Evaluated against 3D extruded prism [Z_ground - depth - tol, Z_ground + tol].
   - QL-B, QL-C, QL-D features do NOT have verified vertical reliability under ASCE 38-22.
     They must NEVER be cleared on the basis of unverified Z coordinates; any horizontal
     overlap within the 2D excavation buffer triggers a tolerance zone alert.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import shapely
from shapely.geometry import shape

from app.crs import (
    STORAGE_SRID,
    from_storage,
    get_transformer,
    to_storage,
)
from models.utility import (
    GeoJsonFeature,
    GeoJsonFeatureCollection,
    QualityLevel,
    UtilityFeature,
)
from schemas.clash import (
    ClashDetail,
    ClashDetectionResult,
    ClashSummary,
    TicketInputModel,
    get_tolerance_m,
)


def get_utm_epsg_for_lon_lat(lon: float, lat: float) -> int:
    """Determine the appropriate UTM zone EPSG code (metric) for a given lon/lat."""
    zone = int((lon + 180) / 6) + 1
    if lat >= 0:
        return 32600 + zone
    return 32700 + zone


def build_postgis_clash_query(
    ticket_id: str,
    metric_srid: int,
    tolerance_m: float,
    excavation_depth_m: float,
    deg_margin: float = 0.01,
) -> str:
    """Generate the PostGIS 3D clash detection SQL query using ST_3DDWithin and ST_Extrude."""
    return f"""
    WITH ticket AS (
        SELECT
            ticket_id,
            geom AS geom_4326,
            ST_Transform(geom, {metric_srid}) AS geom_metric,
            {excavation_depth_m}::float8 AS depth_m
        FROM ticket_polygons
        WHERE ticket_id = '{ticket_id}'
    )
    SELECT
        u.id,
        u.feature_type,
        u.quality_level,
        u.geometry_type,
        u.material,
        u.utility_type,
        u.horizontal_accuracy,
        u.vertical_accuracy,
        u.nominal_diameter_mm,
        sue_geojson_geometry(u.geom) AS geojson_geom,
        -- Evaluate 3D distance against extruded excavation prism
        ST_3DDWithin(
            ST_Transform(u.geom, {metric_srid}),
            ST_Extrude(ticket.geom_metric, 0, 0, -ticket.depth_m),
            {tolerance_m} + u.horizontal_accuracy
        ) AS is_clash
    FROM utility_features u
    CROSS JOIN ticket
    WHERE u.geom && ST_Expand(ticket.geom_4326, {deg_margin})
      AND (
          -- QL-A: True 3D clash check against extruded volume
          (u.quality_level = 'QL-A' AND ST_3DDWithin(
              ST_Transform(u.geom, {metric_srid}),
              ST_Extrude(ticket.geom_metric, 0, 0, -ticket.depth_m),
              {tolerance_m} + u.horizontal_accuracy
          ))
          OR
          -- QL-B/C/D: Vertical unverified per ASCE 38-22; enforce 2D horizontal buffer
          (u.quality_level <> 'QL-A' AND ST_DWithin(
              ST_Transform(u.geom, {metric_srid}),
              ticket.geom_metric,
              {tolerance_m} + u.horizontal_accuracy
          ))
      );
    """


def evaluate_3d_clash(
    utility: UtilityFeature,
    ticket_poly_m: Any,  # Shapely 2D geometry in metric CRS
    z_min_excavation: float,
    z_max_excavation: float,
    tolerance_m: float,
    metric_transformer: Any,
) -> tuple[str, float, str]:
    """Perform 3D collision detection and clearance computation for a single utility asset.

    Returns:
        (status, calculated_3d_clearance_m, mitigation_recommendation)
    """
    raw_geom = utility["geom"]
    ql = utility["quality_level"]
    horiz_acc = float(utility["horizontal_accuracy"])
    vert_acc = float(utility["vertical_accuracy"])
    nominal_dia_m = float(utility["nominal_diameter_mm"]) / 1000.0

    # Total effective tolerance includes pipe half-diameter and positional accuracy
    total_tol_m = tolerance_m + (nominal_dia_m / 2.0) + horiz_acc

    # Convert 3D utility geometry to shapely and project to metric coordinates
    geom_4326 = shape(raw_geom)
    geom_m = shapely.transform(
        geom_4326,
        lambda coords: np.column_stack(metric_transformer.transform(coords[:, 0], coords[:, 1])),
    )

    # 1. 2D Horizontal distance to excavation polygon
    h_dist_m = float(ticket_poly_m.distance(geom_m))
    h_intersects = ticket_poly_m.intersects(geom_m)

    # Extract Z coordinates (in meters) from utility
    z_coords: list[float] = []

    def _extract_z(coords: Any) -> None:
        if isinstance(coords, (list, tuple)):
            if len(coords) >= 3 and isinstance(coords[0], (int, float)):
                z_coords.append(float(coords[2]))
            else:
                for sub in coords:
                    _extract_z(sub)

    _extract_z(raw_geom.get("coordinates", []))
    u_z_min = min(z_coords) if z_coords else 0.0
    u_z_max = max(z_coords) if z_coords else 0.0

    # ASCE 38-22 Vertical Reliability Check:
    # QL-A has verified vertical survey. QL-B, QL-C, QL-D do NOT.
    if ql != QualityLevel.QL_A.value:
        # Vertical is indeterminate / unverified: do not allow Z to clear the asset
        if h_intersects or h_dist_m == 0.0:
            return (
                "COLLISION",
                0.0,
                f"Direct hit within excavation footprint. ASCE {ql} requires "
                "non-destructive vacuum exposure (potholing) to verify depth.",
            )
        if h_dist_m <= total_tol_m:
            return (
                "WITHIN_TOLERANCE_ZONE",
                round(h_dist_m, 3),
                f"Asset within statutory tolerance zone "
                f"({round(h_dist_m, 2)}m <= {round(total_tol_m, 2)}m). "
                "Hand digging / vacuum excavation required.",
            )
        return ("CLEAR", round(h_dist_m, 3), "Clear of statutory tolerance zone.")

    # For QL-A: 3D Clashing with verified Z
    # Expand vertical boundaries by vertical accuracy and pipe radius
    z_clearance_top = (u_z_min - (nominal_dia_m / 2.0) - vert_acc) - z_max_excavation
    z_clearance_bottom = z_min_excavation - (u_z_max + (nominal_dia_m / 2.0) + vert_acc)
    v_overlap = not (z_clearance_top > 0 or z_clearance_bottom > 0)

    # Calculate 3D clearance distance
    v_dist_m = 0.0
    if z_clearance_top > 0:
        v_dist_m = z_clearance_top
    elif z_clearance_bottom > 0:
        v_dist_m = z_clearance_bottom

    clearance_3d_m = math.hypot(h_dist_m, v_dist_m)

    if h_intersects and v_overlap:
        return (
            "COLLISION",
            0.0,
            "Critical 3D Collision: Utility physically crosses excavation prism volume. "
            "Immediate design realignment required.",
        )
    if clearance_3d_m <= total_tol_m:
        return (
            "WITHIN_TOLERANCE_ZONE",
            round(clearance_3d_m, 3),
            f"Within 3D tolerance zone ({round(clearance_3d_m, 2)}m <= {round(total_tol_m, 2)}m). "
            "Potholing verification required.",
        )
    return (
        "CLEAR",
        round(clearance_3d_m, 3),
        "Asset verified outside 3D excavation clearance envelope.",
    )


def detect_clashes(
    ticket: TicketInputModel,
    utilities: list[UtilityFeature],
    export_srid: int | None = None,
) -> ClashDetectionResult:
    """Execute real-time 3D clash detection for an 811 ticket against utility inventory."""
    # Ensure ticket polygon is in EPSG:4326
    raw_poly = ticket.polygon
    if ticket.source_srid != STORAGE_SRID:
        raw_poly = to_storage(raw_poly, ticket.source_srid)

    ticket_geom_4326 = shape(raw_poly)
    centroid = ticket_geom_4326.centroid
    metric_srid = get_utm_epsg_for_lon_lat(centroid.x, centroid.y)

    # Metric transformer for accurate distance calculations
    transformer = get_transformer(STORAGE_SRID, metric_srid)
    ticket_poly_m = shapely.transform(
        ticket_geom_4326,
        lambda coords: np.column_stack(transformer.transform(coords[:, 0], coords[:, 1])),
    )

    # Excavation depth bounds in meters
    z_ground = ticket.ground_elevation_m
    z_max_excavation = z_ground
    z_min_excavation = z_ground - ticket.excavation_depth_m

    # Statutory tolerance zone in meters
    tolerance_m = get_tolerance_m(ticket.state_code, ticket.tolerance_in_override)
    statutory_inches = (
        ticket.tolerance_in_override
        if ticket.tolerance_in_override is not None
        else (tolerance_m / 0.0254)
    )

    clash_details: list[ClashDetail] = []
    geojson_features: list[GeoJsonFeature] = []
    quality_counts: dict[str, int] = {"QL-A": 0, "QL-B": 0, "QL-C": 0, "QL-D": 0}
    total_clashes = 0

    for u in utilities:
        status, clearance_m, mitigation = evaluate_3d_clash(
            utility=u,
            ticket_poly_m=ticket_poly_m,
            z_min_excavation=z_min_excavation,
            z_max_excavation=z_max_excavation,
            tolerance_m=tolerance_m,
            metric_transformer=transformer,
        )

        is_clash_asset = status in ("COLLISION", "WITHIN_TOLERANCE_ZONE")
        if is_clash_asset:
            total_clashes += 1
            quality_counts[u["quality_level"]] = quality_counts.get(u["quality_level"], 0) + 1

        clash_details.append(
            {
                "utility_id": u["id"],
                "feature_type": u["feature_type"],
                "quality_level": u["quality_level"],
                "geometry_type": u["geometry_type"],
                "material": u["material"],
                "utility_type": u["utility_type"],
                "horizontal_accuracy_m": u["horizontal_accuracy"],
                "vertical_accuracy_m": u["vertical_accuracy"],
                "tolerance_zone_m": round(tolerance_m, 3),
                "nominal_diameter_mm": u["nominal_diameter_mm"],
                "calculated_3d_clearance_m": clearance_m,
                "status": status,  # type: ignore[typeddict-item]
                "mitigation_recommendation": mitigation,
            }
        )

        # Build GeoJSON feature representation
        out_geom = u["geom"]
        if export_srid is not None and export_srid != STORAGE_SRID:
            out_geom = from_storage(out_geom, export_srid)

        geojson_features.append(
            {
                "type": "Feature",
                "id": u["id"],
                "geometry": out_geom,
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
                    "tolerance_zone_m": round(tolerance_m, 3),
                    "is_clash": is_clash_asset,
                    "clash_reason": status if is_clash_asset else None,
                },
            }
        )

    summary: ClashSummary = {
        "ticket_id": ticket.ticket_id,
        "total_utilities_evaluated": len(utilities),
        "total_clashes": total_clashes,
        "quality_breakdown": quality_counts,
        "statutory_tolerance_in": round(statutory_inches, 1),
        "tolerance_buffer_m": round(tolerance_m, 3),
    }

    feature_collection: GeoJsonFeatureCollection = {
        "type": "FeatureCollection",
        "features": geojson_features,
    }

    return {
        "summary": summary,
        "clashes": clash_details,
        "geojson": feature_collection,
    }
