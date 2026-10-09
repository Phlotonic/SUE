"""Tests for the CRS transformation pipeline (app.crs).

Reference values:

* EPSG Guidance Note 7-2 (IOGP Publication 373-7-2), Lambert Conic Conformal (2SP) worked
  example, NAD27 / Texas South Central (EPSG:32040): 28°30'00"N, 96°00'00"W ->
  E = 2963503.91 ftUS, N = 254759.80 ftUS.
* EPSG Guidance Note 7-2, Transverse Mercator worked example, OSGB 1936 / British National
  Grid (EPSG:27700): 50°30'00"N, 00°30'00"E -> E = 577274.99 m, N = 69740.50 m.
* SPCS83 Texas Central zone definition (NOAA Manual NOS NGS 5): latitude of origin 29°40'N,
  central meridian 100°20'W, false easting 700 000 m, false northing 3 000 000 m. The origin
  must map exactly to (FE, FN); this is used as the control value for the projection inverse
  because no NGS datasheet could be retrieved from this environment.
"""

from __future__ import annotations

import copy
import math
import time

import pyproj
import pytest
from pyproj import Geod

from app import crs
from app.crs import STORAGE_SRID, CRSError

FT_US = 1200.0 / 3937.0  # meters per US survey foot (exact by definition)
FT_INTL = 0.3048  # meters per international foot (exact by definition)
GRS80 = Geod(ellps="GRS80")

# A point in downtown Austin, TX expressed in NAD83(2011) / Texas Central (ftUS).
AUSTIN_6578 = (3115000.0, 10070000.0)


def dms(d: float, m: float = 0.0, s: float = 0.0) -> float:
    return d + m / 60.0 + s / 3600.0


def geodesic_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    return GRS80.inv(lon1, lat1, lon2, lat2)[2]


# --------------------------------------------------------------------------------------
# 1. Projection engine vs independently published worked examples (no datum involved)
# --------------------------------------------------------------------------------------


def test_gn7_2_lambert_conic_conformal_2sp_texas_south_central_nad27():
    lon, lat = -dms(96), dms(28, 30)
    [(e, n, z)] = crs.transform_coords([(lon, lat)], 4267, 32040)
    assert e == pytest.approx(2963503.91, abs=0.01)
    assert n == pytest.approx(254759.80, abs=0.01)
    assert z == 0.0


def test_gn7_2_lambert_conic_conformal_2sp_inverse():
    [(lon, lat, _)] = crs.transform_coords([(2963503.91, 254759.80)], 32040, 4267)
    # 0.01 ftUS rounding of the published grid values ~ 3 mm on the ground
    assert geodesic_m(lon, lat, -96.0, 28.5) < 0.005


def test_gn7_2_transverse_mercator_british_national_grid():
    [(e, n, _)] = crs.transform_coords([(dms(0, 30), dms(50, 30))], 4277, 27700)
    assert e == pytest.approx(577274.99, abs=0.01)
    assert n == pytest.approx(69740.50, abs=0.01)


# --------------------------------------------------------------------------------------
# 2. Sub-millimetre round trips SPCS -> storage -> SPCS
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("srid", [6578, 2277])
def test_round_trip_texas_central_ftus_sub_millimetre(srid):
    pts = [
        (AUSTIN_6578[0], AUSTIN_6578[1], 100.0),
        (2296583.333, 9842500.0, -12.5),
        (3500000.0, 10500000.0, 0.0),
        (2800000.0, 9900000.0, 543.21),
    ]
    stored = crs.transform_coords(pts, srid, STORAGE_SRID)
    back = crs.transform_coords(stored, STORAGE_SRID, srid)
    for (x0, y0, z0), (x1, y1, z1) in zip(pts, back, strict=True):
        horiz_m = math.hypot(x1 - x0, y1 - y0) * FT_US
        assert horiz_m < 0.001
        assert z1 == pytest.approx(z0, abs=1e-9)


def test_round_trip_z_is_exact_unit_conversion():
    [(_, _, z_m)] = crs.transform_coords([(*AUSTIN_6578, 100.0)], 6578, STORAGE_SRID)
    assert z_m == pytest.approx(100.0 * FT_US, abs=1e-12)


# --------------------------------------------------------------------------------------
# 3. Projection inverse vs control value (SPCS83 Texas Central origin)
# --------------------------------------------------------------------------------------

TX_C_LON0 = -dms(100, 20)
TX_C_LAT0 = dms(29, 40)


def test_spcs_inverse_origin_metric_maps_to_nad83_2011_geographic():
    [(lon, lat, _)] = crs.transform_coords([(700000.0, 3000000.0)], 6577, 6318)
    assert geodesic_m(lon, lat, TX_C_LON0, TX_C_LAT0) < 0.01


def test_spcs_inverse_origin_ftus_maps_to_nad83_2011_geographic():
    # EPSG publishes FE = 2296583.333 ftUS (700 000 m rounded to 0.001 ftUS = 0.1 mm).
    [(lon, lat, _)] = crs.transform_coords([(2296583.333, 9842500.0)], 6578, 6318)
    assert geodesic_m(lon, lat, TX_C_LON0, TX_C_LAT0) < 0.01


def test_spcs_forward_origin_maps_to_false_easting_northing():
    [(e, n, _)] = crs.transform_coords([(TX_C_LON0, TX_C_LAT0)], 6318, 6577)
    assert e == pytest.approx(700000.0, abs=0.001)
    assert n == pytest.approx(3000000.0, abs=0.001)


# --------------------------------------------------------------------------------------
# 4. Z unit conversion
# --------------------------------------------------------------------------------------


def test_z_ftus_to_meters_on_ingest():
    [(_, _, z)] = crs.transform_coords([(*AUSTIN_6578, 10.0)], 6578, STORAGE_SRID)
    assert z == pytest.approx(3.048006096012192, abs=1e-12)


def test_z_meters_to_ftus_on_export():
    stored = crs.to_storage({"type": "Point", "coordinates": [*AUSTIN_6578, 10.0]}, 6578)
    assert stored["coordinates"][2] == pytest.approx(3.048006096012192, abs=1e-12)
    out = crs.from_storage(stored, 6578)
    assert out["coordinates"][2] == pytest.approx(10.0, abs=1e-9)


def test_z_international_feet():
    assert crs.linear_unit_factor(2223) == pytest.approx(FT_INTL, abs=1e-15)
    [(lon, lat, _)] = crs.transform_coords([(700000.0, 1000000.0)], 2223, STORAGE_SRID)
    [(_, _, z)] = crs.transform_coords([(lon, lat, 3.048)], STORAGE_SRID, 2223)
    assert z == pytest.approx(10.0, abs=1e-9)


@pytest.mark.parametrize(
    ("srid", "factor"),
    [(6578, FT_US), (2277, FT_US), (2223, FT_INTL), (26986, 1.0), (6577, 1.0), (4326, 1.0)],
)
def test_linear_unit_factor(srid, factor):
    assert crs.linear_unit_factor(srid) == pytest.approx(factor, abs=1e-15)


def test_linear_unit_factor_ftus_value():
    assert crs.linear_unit_factor(6578) == pytest.approx(0.3048006096012192, abs=1e-16)


# --------------------------------------------------------------------------------------
# 5. State Plane detection from CRS metadata
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "srid",
    [
        2277,  # NAD83 / Texas Central (ftUS)
        6578,  # NAD83(2011) / Texas Central (ftUS)
        6577,  # NAD83(2011) / Texas Central (m)
        2229,  # NAD83 / California zone 5 (ftUS)
        26986,  # NAD83 / Massachusetts Mainland (m)
        2223,  # NAD83 / Arizona Central (international ft)
        2918,  # NAD83(HARN) / Texas Central (ftUS)
        3664,  # NAD83(NSRS2007) / Texas Central (ftUS)
        26931,  # NAD83 / Alaska zone 1 (Hotine Oblique Mercator)
    ],
)
def test_is_nad83_state_plane_true(srid):
    assert crs.is_nad83_state_plane(srid) is True


@pytest.mark.parametrize(
    "srid",
    [
        3857,  # WGS 84 / Pseudo-Mercator
        26914,  # NAD83 / UTM zone 14N
        6339,  # NAD83(2011) / UTM zone 10N
        4326,  # WGS 84 geographic
        4269,  # NAD83 geographic
        6318,  # NAD83(2011) geographic
        5070,  # NAD83 / Conus Albers
        6350,  # NAD83(2011) / Conus Albers
        3081,  # NAD83 / Texas State Mapping System (LCC, but not SPCS)
        3666,  # NAD83(NSRS2007) / Texas Centric Lambert Conformal
        32040,  # NAD27 / Texas South Central (SPCS27)
        999999,  # unknown
    ],
)
def test_is_nad83_state_plane_false(srid):
    assert crs.is_nad83_state_plane(srid) is False


# --------------------------------------------------------------------------------------
# 6. GeoJSON geometries
# --------------------------------------------------------------------------------------

X0, Y0 = AUSTIN_6578


def _square(x, y, d):
    return [[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]


def _all_positions(coords):
    if coords and isinstance(coords[0], (int, float)):
        yield coords
    else:
        for c in coords:
            yield from _all_positions(c)


def test_point_2d_becomes_3d():
    out = crs.to_storage({"type": "Point", "coordinates": [X0, Y0]}, 6578)
    assert out["type"] == "Point"
    lon, lat, z = out["coordinates"]
    assert z == 0.0
    assert isinstance(out["coordinates"], list)


def test_polygon_with_hole():
    geom = {
        "type": "Polygon",
        "coordinates": [_square(X0, Y0, 1000.0), _square(X0 + 100, Y0 + 100, 100.0)],
    }
    original = copy.deepcopy(geom)
    out = crs.to_storage(geom, 6578)
    assert geom == original, "input must not be mutated"
    assert out["type"] == "Polygon"
    assert len(out["coordinates"]) == 2
    for ring in out["coordinates"]:
        assert len(ring) == 5
        assert ring[0] == ring[-1], "rings must stay closed"
        assert all(len(p) == 3 and isinstance(p, list) for p in ring)
    # hole lies strictly inside the shell
    shell_lons = [p[0] for p in out["coordinates"][0]]
    hole_lons = [p[0] for p in out["coordinates"][1]]
    assert min(shell_lons) < min(hole_lons) < max(hole_lons) < max(shell_lons)


def test_multilinestring_mixed_2d_3d():
    geom = {
        "type": "MultiLineString",
        "coordinates": [
            [[X0, Y0, -5.0], [X0 + 10, Y0 + 10, -6.0]],
            [[X0 + 20, Y0], [X0 + 30, Y0 + 5]],
        ],
    }
    out = crs.to_storage(geom, 6578)
    assert out["type"] == "MultiLineString"
    zs = [p[2] for p in _all_positions(out["coordinates"])]
    assert zs == pytest.approx([-5.0 * FT_US, -6.0 * FT_US, 0.0, 0.0], abs=1e-12)


def test_multipoint_and_multipolygon():
    mp = crs.to_storage({"type": "MultiPoint", "coordinates": [[X0, Y0], [X0 + 1, Y0]]}, 6578)
    assert len(mp["coordinates"]) == 2 and all(len(p) == 3 for p in mp["coordinates"])
    mpoly = crs.to_storage(
        {
            "type": "MultiPolygon",
            "coordinates": [[_square(X0, Y0, 10.0)], [_square(X0 + 50, Y0, 10.0)]],
        },
        6578,
    )
    assert len(mpoly["coordinates"]) == 2
    assert all(len(p) == 3 for p in _all_positions(mpoly["coordinates"]))


def test_geometry_collection():
    geom = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Point", "coordinates": [X0, Y0, 3.0]},
            {"type": "LineString", "coordinates": [[X0, Y0], [X0 + 100, Y0 + 100]]},
            {
                "type": "GeometryCollection",
                "geometries": [{"type": "Polygon", "coordinates": [_square(X0, Y0, 5.0)]}],
            },
        ],
    }
    out = crs.to_storage(geom, 6578)
    assert out["type"] == "GeometryCollection"
    assert [g["type"] for g in out["geometries"]] == ["Point", "LineString", "GeometryCollection"]
    assert out["geometries"][0]["coordinates"][2] == pytest.approx(3.0 * FT_US, abs=1e-12)
    inner = out["geometries"][2]["geometries"][0]
    assert inner["type"] == "Polygon" and len(inner["coordinates"][0]) == 5


def test_geometry_round_trip_through_storage():
    geom = {"type": "LineString", "coordinates": [[X0, Y0, -4.0], [X0 + 250.0, Y0 - 75.0, -4.5]]}
    back = crs.from_storage(crs.to_storage(geom, 6578), 6578)
    for p0, p1 in zip(geom["coordinates"], back["coordinates"], strict=True):
        assert math.hypot(p1[0] - p0[0], p1[1] - p0[1]) * FT_US < 0.001
        assert p1[2] == pytest.approx(p0[2], abs=1e-9)


def test_to_storage_from_4326_only_ensures_3d():
    geom = {"type": "LineString", "coordinates": [[-97.7, 30.2], [-97.71, 30.21, 1.5]]}
    out = crs.to_storage(geom, 4326)
    assert out["coordinates"] == [[-97.7, 30.2, 0.0], [-97.71, 30.21, 1.5]]


def test_transform_geometry_default_target_is_storage():
    a = crs.transform_geometry({"type": "Point", "coordinates": [X0, Y0]}, 6578)
    b = crs.to_storage({"type": "Point", "coordinates": [X0, Y0]}, 6578)
    assert a == b


@pytest.mark.parametrize(
    "geom",
    [
        {"type": "Feature", "geometry": None},
        {"type": "Circle", "coordinates": [0, 0]},
        {"coordinates": [0, 0]},
        {"type": "Point"},
        {"type": "Point", "coordinates": [1.0]},
        {"type": "Point", "coordinates": [1.0, 2.0, 3.0, 4.0]},
        {"type": "Point", "coordinates": ["a", "b"]},
        {"type": "LineString", "coordinates": 5},
        {"type": "GeometryCollection"},
    ],
)
def test_invalid_geometry_raises(geom):
    with pytest.raises(CRSError):
        crs.to_storage(geom, 6578)


def test_from_storage_requires_state_plane_by_default():
    geom = {"type": "Point", "coordinates": [-97.74, 30.27, 1.0]}
    with pytest.raises(CRSError):
        crs.from_storage(geom, 26914)
    out = crs.from_storage(geom, 26914, require_state_plane=False)
    assert 600000 < out["coordinates"][0] < 700000  # UTM 14N easting near Austin


# --------------------------------------------------------------------------------------
# 7. Caching and vectorisation
# --------------------------------------------------------------------------------------


def test_get_transformer_is_cached():
    t1 = crs.get_transformer(6578, STORAGE_SRID)
    t2 = crs.get_transformer(6578, STORAGE_SRID)
    assert t1 is t2
    assert crs.get_transformer.cache_info().hits >= 1


def test_transform_is_vectorised_single_call(monkeypatch):
    real = crs.get_transformer(6578, STORAGE_SRID)
    calls: list[int] = []

    class Spy:
        def transform(self, xx, yy, *args, **kwargs):
            calls.append(len(xx))
            return real.transform(xx, yy, *args, **kwargs)

    monkeypatch.setattr(crs, "get_transformer", lambda s, t: Spy())
    geom = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Polygon", "coordinates": [_square(X0, Y0, 10.0)]},
            {"type": "MultiPoint", "coordinates": [[X0, Y0], [X0 + 1, Y0 + 1]]},
        ],
    }
    crs.to_storage(geom, 6578)
    assert calls == [7], "all positions must go to PROJ in one vectorised call"


def test_bulk_performance():
    n = 50_000
    pts = [(X0 + i * 0.5, Y0 + i * 0.25, float(i % 7)) for i in range(n)]
    crs.transform_coords(pts[:10], 6578, STORAGE_SRID)  # warm cache
    t0 = time.perf_counter()
    out = crs.transform_coords(pts, 6578, STORAGE_SRID)
    elapsed = time.perf_counter() - t0
    assert len(out) == n
    assert elapsed < 2.0, f"50k points took {elapsed:.3f}s"


# --------------------------------------------------------------------------------------
# 8. Axis order
# --------------------------------------------------------------------------------------


def test_axis_order_lon_lat():
    [(lon, lat, _)] = crs.transform_coords([AUSTIN_6578], 6578, STORAGE_SRID)
    assert -98.0 < lon < -97.0
    assert 30.0 < lat < 31.0


def test_axis_order_storage_to_spcs():
    [(e, n, _)] = crs.transform_coords([(-97.74, 30.27)], STORAGE_SRID, 6578)
    assert 3_000_000 < e < 3_200_000  # easting first
    assert 10_000_000 < n < 10_200_000


# --------------------------------------------------------------------------------------
# 9. Errors
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [999999, 0, -1, 5703, 4979])
def test_invalid_srid_raises(bad):
    # 5703 = NAVD88 height (vertical only), 4979 = WGS 84 3D: not 2D horizontal CRSs
    with pytest.raises(CRSError):
        crs.get_transformer(bad, STORAGE_SRID)
    with pytest.raises(CRSError):
        crs.transform_coords([(0.0, 0.0)], STORAGE_SRID, bad)


def test_invalid_srid_type_raises():
    with pytest.raises(CRSError):
        crs.get_transformer("EPSG:6578", STORAGE_SRID)  # type: ignore[arg-type]


def test_linear_unit_factor_unknown_raises():
    with pytest.raises(CRSError):
        crs.linear_unit_factor(999999)


@pytest.mark.parametrize(
    "pts",
    [
        [(float("nan"), 10070000.0)],
        [(3115000.0, float("inf"))],
        [(3115000.0, 10070000.0, float("nan"))],
    ],
)
def test_non_finite_input_raises(pts):
    with pytest.raises(CRSError):
        crs.transform_coords(pts, 6578, STORAGE_SRID)


def test_non_finite_output_raises():
    with pytest.raises(CRSError):
        crs.transform_coords([(-97.0, 95.0)], STORAGE_SRID, 6578)  # latitude > 90


@pytest.mark.parametrize("pts", [[(1.0,)], [(1.0, 2.0, 3.0, 4.0)], [("x", 2.0)]])
def test_bad_position_shape_raises(pts):
    with pytest.raises(CRSError):
        crs.transform_coords(pts, 6578, STORAGE_SRID)


def test_empty_coords():
    assert crs.transform_coords([], 6578, STORAGE_SRID) == []


# --------------------------------------------------------------------------------------
# Datum characterisation (documents what PROJ actually does; see app.crs docstring)
# --------------------------------------------------------------------------------------


def test_network_disabled():
    assert pyproj.network.is_network_enabled() is False


def test_nad83_2011_to_wgs84_is_null_transformation_with_2m_accuracy():
    t = crs.get_transformer(6318, STORAGE_SRID)
    assert "NAD83(2011) to WGS 84 (1)" in t.description
    assert t.accuracy == pytest.approx(2.0)
    lon, lat = -97.74, 30.27
    [(lon2, lat2, _)] = crs.transform_coords([(lon, lat)], 6318, STORAGE_SRID)
    assert (lon2, lat2) == (lon, lat), "null transformation: numerically identical"


def test_nad83_1986_spcs_to_wgs84_uses_null_transformation_4m():
    t = crs.get_transformer(2277, STORAGE_SRID)
    assert "NAD83 to WGS 84 (1)" in t.description
    assert t.accuracy == pytest.approx(4.0)


def test_known_realization_inconsistency_harn_vs_2011():
    """Same numeric SPCS coordinates in HARN vs 2011 land ~1 m apart in storage.

    PROJ applies a non-null NAD83(HARN)->WGS 84 operation but a null NAD83(2011)->WGS 84
    one. Characterises a compliance hazard (see module docstring); it is NOT a desired
    property. If this starts failing, PROJ changed its choice and the docstring must be
    revisited.
    """
    [(a_lon, a_lat, _)] = crs.transform_coords([AUSTIN_6578], 2918, STORAGE_SRID)
    [(b_lon, b_lat, _)] = crs.transform_coords([AUSTIN_6578], 6578, STORAGE_SRID)
    d = geodesic_m(a_lon, a_lat, b_lon, b_lat)
    assert 0.3 < d < 3.0
