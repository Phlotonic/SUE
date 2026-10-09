"""Coordinate Reference System (CRS) Transformation Pipeline.

This module provides high-throughput, vectorized 2D/3D coordinate transformations
between state plane coordinate systems (SPCS83) and WGS84 (EPSG:4326), with strict
preservation of vertical unit scales and ASCE 38-22 / ASCE 75-22 spatial integrity.

Architectural Decisions & Geodetic Findings:
--------------------------------------------
1. Storage CRS: All geometry is stored in EPSG:4326 (WGS84 2D geographic lon/lat) with
   explicit 3D Z coordinates in meters.
2. Datum Transformations:
   - NAD83(2011) (EPSG:6318) to WGS84 (EPSG:4326) utilizes PROJ's default operation
     'NAD83(2011) to WGS 84 (1)', which is a null (identity) transformation with a
     catalogued uncertainty / accuracy of 2.0 meters.
   - NAD83(1986) (e.g. EPSG:2277) to WGS84 uses 'NAD83 to WGS 84 (1)' with an accuracy
     of 4.0 meters.
   - NAD83(HARN) (e.g. EPSG:2918) applies a non-null Helmert shift (~0.9m offset compared
     to NAD83(2011)).
   - True sub-centimeter geodetic alignment between tectonic NAD83 realizations and WGS84
     requires time-dependent kinematic velocity models (HTDP). However, the projection
     inverse from State Plane (SPCS83) to NAD83 geographic coordinates is purely
     mathematical and accurate to sub-millimeter tolerances.
3. Vertical Datum: Z coordinates are treated as orthometric heights / depths. Ingestion
   converts horizontal linear units (e.g. US Survey Feet, International Feet) to meters;
   export converts meters back to target linear units.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Mapping, Sequence
from typing import Any

import pyproj

# Disable network lookups for deterministic offline transformation
pyproj.network.set_network_enabled(False)

STORAGE_SRID: int = 4326
FT_US: float = 1200.0 / 3937.0  # Exact US Survey Foot in meters
FT_INTL: float = 0.3048  # Exact International Foot in meters


class CRSError(ValueError):
    """Raised when an invalid SRID, unsupported geometry, or projection error occurs."""


def _get_valid_crs(srid: int) -> pyproj.CRS:
    """Validate and return a 2D horizontal CRS for the given SRID."""
    if not isinstance(srid, int) or isinstance(srid, bool) or srid <= 0:
        raise CRSError(f"Invalid SRID: {srid}. Expected positive integer.")
    try:
        crs_obj = pyproj.CRS.from_user_input(srid)
    except Exception as exc:
        raise CRSError(f"Unable to parse SRID {srid}: {exc}") from exc

    # Ensure it is a 2D horizontal system (projected or 2D geographic)
    if crs_obj.type_name not in ("Projected CRS", "Geographic 2D CRS"):
        raise CRSError(
            f"SRID {srid} is of type '{crs_obj.type_name}'. "
            "Only 2D Projected CRS or Geographic 2D CRS are supported."
        )
    if len(crs_obj.axis_info) != 2:
        raise CRSError(
            f"SRID {srid} has {len(crs_obj.axis_info)} axes. Expected 2 horizontal axes."
        )
    return crs_obj


def is_nad83_state_plane(srid: int) -> bool:
    """Determine whether an SRID corresponds to an NAD83 State Plane Coordinate System (SPCS83).

    Checks pyproj CRS metadata rather than a static list:
    - Must be a projected CRS
    - Datum must belong to the NAD83 family
    - Coordinate operation must start with 'SPCS83'
    """
    try:
        crs_obj = _get_valid_crs(srid)
    except CRSError:
        return False

    if not crs_obj.is_projected:
        return False

    datum = crs_obj.datum
    datum_name = datum.name if datum else ""
    if not ("NAD83" in datum_name or "North American Datum 1983" in datum_name):
        return False

    op = crs_obj.coordinate_operation
    if not op or not op.name or not op.name.startswith("SPCS83"):
        return False

    return True


def linear_unit_factor(srid: int) -> float:
    """Return meters per horizontal linear unit for the given SRID.

    Geographic systems (in degrees) return 1.0 (assuming Z is in meters).
    """
    crs_obj = _get_valid_crs(srid)
    if crs_obj.is_geographic:
        return 1.0

    unit_name = crs_obj.axis_info[0].unit_name.lower()
    if "us survey foot" in unit_name or "us survey ft" in unit_name:
        return FT_US
    if "foot" in unit_name or "ft" in unit_name:
        return FT_INTL
    if "metre" in unit_name or "meter" in unit_name:
        return 1.0

    factor = crs_obj.axis_info[0].unit_conversion_factor
    if factor is not None:
        return float(factor)
    raise CRSError(f"Could not determine linear unit factor for SRID {srid}")


@functools.lru_cache(maxsize=128)
def get_transformer(source_srid: int, target_srid: int) -> pyproj.Transformer:
    """Return a cached, thread-safe pyproj Transformer with always_xy=True."""
    _get_valid_crs(source_srid)
    _get_valid_crs(target_srid)
    try:
        return pyproj.Transformer.from_crs(source_srid, target_srid, always_xy=True)
    except Exception as exc:
        raise CRSError(
            f"Failed to create transformer from {source_srid} to {target_srid}: {exc}"
        ) from exc


def transform_coords(
    coords: Sequence[Sequence[float]],
    source_srid: int,
    target_srid: int,
) -> list[tuple[float, float, float]]:
    """Transform a sequence of 2D or 3D positions vectorized into 3D (x, y, z) tuples."""
    if not coords:
        return []

    # Validate coordinates shape and finiteness
    xx: list[float] = []
    yy: list[float] = []
    zz_in: list[float] = []

    for pt in coords:
        if not isinstance(pt, (list, tuple)) or len(pt) not in (2, 3):
            raise CRSError(f"Coordinate {pt!r} must be a 2D or 3D sequence")
        try:
            x = float(pt[0])
            y = float(pt[1])
            z = float(pt[2]) if len(pt) == 3 else 0.0
        except (ValueError, TypeError) as exc:
            raise CRSError(f"Coordinate contains non-numeric value: {pt!r}") from exc

        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            raise CRSError(f"Non-finite coordinate encountered: {pt!r}")

        xx.append(x)
        yy.append(y)
        zz_in.append(z)

    src_factor = linear_unit_factor(source_srid)
    tgt_factor = linear_unit_factor(target_srid)
    z_scale = src_factor / tgt_factor

    if source_srid == target_srid:
        out_x, out_y = xx, yy
    else:
        transformer = get_transformer(source_srid, target_srid)
        try:
            out_x, out_y = transformer.transform(xx, yy)
        except Exception as exc:
            raise CRSError(f"Transformation failed: {exc}") from exc

    results: list[tuple[float, float, float]] = []
    for ox, oy, z_orig in zip(out_x, out_y, zz_in, strict=True):
        fox = float(ox)
        foy = float(oy)
        foz = float(z_orig * z_scale)
        if not (math.isfinite(fox) and math.isfinite(foy) and math.isfinite(foz)):
            raise CRSError(f"Transformation produced non-finite output: ({fox}, {foy}, {foz})")
        results.append((fox, foy, foz))

    return results


def _collect_positions(
    node: Any,
    positions: list[Sequence[float]],
) -> None:
    """Recursively collect leaf coordinate positions from GeoJSON structure."""
    if not isinstance(node, list):
        raise CRSError(f"Malformed coordinate tree: expected list, got {type(node).__name__}")
    if not node:
        raise CRSError("Empty coordinate array in GeoJSON")
    if isinstance(node[0], (int, float)):
        # Leaf coordinate position
        if len(node) not in (2, 3) or not all(isinstance(v, (int, float)) for v in node):
            raise CRSError(f"Invalid coordinate point: {node!r}")
        positions.append(node)
    else:
        for child in node:
            _collect_positions(child, positions)


def _rebuild_positions(
    node: Any,
    iterator: Any,
) -> list[Any]:
    """Rebuild coordinate structure using transformed 3D coordinate lists."""
    if isinstance(node[0], (int, float)):
        pt = next(iterator)
        return [pt[0], pt[1], pt[2]]
    return [_rebuild_positions(child, iterator) for child in node]


def transform_geometry(
    geom: Mapping[str, Any],
    source_srid: int,
    target_srid: int = STORAGE_SRID,
) -> dict[str, Any]:
    """Transform GeoJSON geometry mapping to target SRID, returning a new 3D GeoJSON dict.

    All coordinates across the geometry are vectorized into a single transformation call.
    """
    if not isinstance(geom, Mapping):
        raise CRSError("Input geometry must be a mapping / dictionary")

    g_type = geom.get("type")
    if not isinstance(g_type, str):
        raise CRSError("Geometry missing valid 'type' attribute")

    # If already in storage SRID and target is storage SRID, ensure coordinates are 3D
    if source_srid == target_srid == STORAGE_SRID:
        pass

    positions: list[Sequence[float]] = []

    def _gather_from_geom(g: Mapping[str, Any]) -> None:
        sub_type = g.get("type")
        if sub_type == "GeometryCollection":
            geoms = g.get("geometries")
            if not isinstance(geoms, list):
                raise CRSError("GeometryCollection missing 'geometries' list")
            for sub_g in geoms:
                if not isinstance(sub_g, Mapping):
                    raise CRSError("Invalid member in GeometryCollection")
                _gather_from_geom(sub_g)
        elif sub_type in (
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        ):
            coords = g.get("coordinates")
            if coords is None:
                raise CRSError(f"{sub_type} missing 'coordinates'")
            _collect_positions(coords, positions)
        else:
            raise CRSError(f"Unsupported geometry type: {sub_type}")

    _gather_from_geom(geom)

    # Vectorized transform for all gathered points
    transformed_pts = transform_coords(positions, source_srid, target_srid)
    pt_iter = iter(transformed_pts)

    def _reconstruct_geom(g: Mapping[str, Any]) -> dict[str, Any]:
        sub_type = g["type"]
        if sub_type == "GeometryCollection":
            return {
                "type": "GeometryCollection",
                "geometries": [_reconstruct_geom(sub_g) for sub_g in g["geometries"]],
            }
        return {
            "type": sub_type,
            "coordinates": _rebuild_positions(g["coordinates"], pt_iter),
        }

    return _reconstruct_geom(geom)


def to_storage(geom: Mapping[str, Any], source_srid: int) -> dict[str, Any]:
    """Transform input geometry to internal WGS84 storage format (EPSG:4326, 3D)."""
    return transform_geometry(geom, source_srid, STORAGE_SRID)


def from_storage(
    geom: Mapping[str, Any],
    target_srid: int,
    *,
    require_state_plane: bool = True,
) -> dict[str, Any]:
    """Export geometry from storage (EPSG:4326) to target projection.

    If require_state_plane is True, target_srid must be a valid NAD83 State Plane.
    """
    if require_state_plane and not is_nad83_state_plane(target_srid):
        raise CRSError(f"Target SRID {target_srid} is not an NAD83 State Plane coordinate system")
    return transform_geometry(geom, STORAGE_SRID, target_srid)
