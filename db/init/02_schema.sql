-- 02_schema.sql — ASCE 38-22 / 75-22 utility schema and 811 ticket storage.
--
-- Invariants
--   * All geometry is stored in EPSG:4326 (lon, lat, Z). Z is meters in the project's
--     vertical frame (NAVD88 orthometric elevation, or depth-below-grade when the
--     ticket's ground_elevation_m is 0). Horizontal distance work is NEVER done on
--     these columns directly (unit = degrees); see backend/app/clash.py.
--   * Every spatial table carries an R-Tree-over-GiST index: USING GIST (geom).
--   * Utility geometry columns use explicit 3D typmods (PointZ, LineStringZ, PolygonZ,
--     PolyhedralSurfaceZ) — one table per ASCE 75-22 geometry_type, unified by the
--     utility_features view.

BEGIN;

-- ---------------------------------------------------------------------------
-- Enumerations
-- ---------------------------------------------------------------------------
CREATE TYPE quality_level AS ENUM ('QL-A', 'QL-B', 'QL-C', 'QL-D');
CREATE TYPE utility_geometry_type AS ENUM ('Point', 'LineString', 'Polygon', '3DObject');
CREATE TYPE utility_feature_type AS ENUM ('Segment', 'Device', 'AccessPoint');

-- ---------------------------------------------------------------------------
-- ASCE 38-22 accuracy thresholds (reference data; mirrored in backend/models/utility.py
-- and enforced by CHECK constraints below).
-- ---------------------------------------------------------------------------
CREATE TABLE quality_level_accuracy (
    quality_level             quality_level PRIMARY KEY,
    max_horizontal_accuracy_m double precision,           -- NULL = no requirement
    max_vertical_accuracy_m   double precision,           -- NULL = no requirement
    vertical_verified         boolean NOT NULL,           -- only QL-A exposes the utility
    description               text NOT NULL
);

INSERT INTO quality_level_accuracy VALUES
    ('QL-A', 0.06, 0.03, true,  'Locate: physically exposed (e.g. vacuum excavation), surveyed'),
    ('QL-B', 0.06, NULL, false, 'Designate: surface geophysics, surveyed horizontal position'),
    ('QL-C', NULL, NULL, false, 'Surveyed visible appurtenances correlated with records'),
    ('QL-D', NULL, NULL, false, 'Existing records / oral recollections');

-- ---------------------------------------------------------------------------
-- 811 tolerance zones by state (inches). Platform invariant: 18–24 in.
-- Unknown states fall back to 24 in (most conservative within the invariant).
-- ---------------------------------------------------------------------------
CREATE TABLE state_tolerance_zones (
    state_code   char(2) PRIMARY KEY CHECK (state_code ~ '^[A-Z]{2}$'),
    tolerance_in numeric(4, 1) NOT NULL CHECK (tolerance_in BETWEEN 18 AND 24),
    adds_half_diameter boolean NOT NULL DEFAULT true,
    source       text NOT NULL
);

INSERT INTO state_tolerance_zones (state_code, tolerance_in, source) VALUES
    ('TX', 18, 'Tex. Util. Code ch. 251: 1/2 nominal diameter + 18 in'),
    ('CA', 24, 'Cal. Gov. Code 4216: 24 in each side of field marking'),
    ('FL', 24, 'Fla. Stat. ch. 556'),
    ('IN', 24, 'Ind. Code 8-1-26'),
    ('MD', 18, 'Md. Public Utilities 12-101'),
    ('NY', 24, '16 NYCRR Part 753'),
    ('NC', 24, 'N.C.G.S. 87-115 et seq.'),
    ('OH', 18, 'Ohio Rev. Code 3781.25'),
    ('OK', 24, 'Okla. Stat. tit. 63 sec. 142'),
    ('SC', 24, 'S.C. Code 58-36');

-- ---------------------------------------------------------------------------
-- Utility feature tables (ASCE 75-22). Shared columns come from a template so all
-- four geometry tables are guaranteed to carry identical attributes and checks.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE utility_feature_template (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    feature_type        utility_feature_type NOT NULL,
    quality_level       quality_level NOT NULL,
    horizontal_accuracy double precision NOT NULL
        CHECK (horizontal_accuracy > 0 AND horizontal_accuracy <= 30),
    vertical_accuracy   double precision NOT NULL
        CHECK (vertical_accuracy > 0 AND vertical_accuracy <= 30),
    material            text NOT NULL CHECK (btrim(material) <> ''),
    utility_type        text NOT NULL CHECK (btrim(utility_type) <> ''),
    owner               text,
    nominal_diameter_mm double precision NOT NULL DEFAULT 0
        CHECK (nominal_diameter_mm >= 0 AND nominal_diameter_mm <= 5000),
    source_srid         integer NOT NULL,
    attributes          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- ASCE 38-22 quality-level accuracy requirements
    CONSTRAINT ql_a_accuracy CHECK (
        quality_level <> 'QL-A' OR (horizontal_accuracy <= 0.06 AND vertical_accuracy <= 0.03)),
    CONSTRAINT ql_b_accuracy CHECK (
        quality_level <> 'QL-B' OR horizontal_accuracy <= 0.06)
);

CREATE TABLE utility_points (
    LIKE utility_feature_template INCLUDING ALL,
    geometry_type utility_geometry_type NOT NULL DEFAULT 'Point'
        CHECK (geometry_type = 'Point'),
    geom geometry(PointZ, 4326) NOT NULL
);

CREATE TABLE utility_segments (
    LIKE utility_feature_template INCLUDING ALL,
    geometry_type utility_geometry_type NOT NULL DEFAULT 'LineString'
        CHECK (geometry_type = 'LineString'),
    geom geometry(LineStringZ, 4326) NOT NULL CHECK (ST_NPoints(geom) >= 2)
);

CREATE TABLE utility_areas (
    LIKE utility_feature_template INCLUDING ALL,
    geometry_type utility_geometry_type NOT NULL DEFAULT 'Polygon'
        CHECK (geometry_type = 'Polygon'),
    geom geometry(PolygonZ, 4326) NOT NULL
);

CREATE TABLE utility_objects (
    LIKE utility_feature_template INCLUDING ALL,
    geometry_type utility_geometry_type NOT NULL DEFAULT '3DObject'
        CHECK (geometry_type = '3DObject'),
    geom geometry(PolyhedralSurfaceZ, 4326) NOT NULL
);

-- R-Tree-over-GiST spatial indexes
CREATE INDEX utility_points_geom_gist   ON utility_points   USING GIST (geom);
CREATE INDEX utility_segments_geom_gist ON utility_segments USING GIST (geom);
CREATE INDEX utility_areas_geom_gist    ON utility_areas    USING GIST (geom);
CREATE INDEX utility_objects_geom_gist  ON utility_objects  USING GIST (geom);

-- Unified read model. UNION ALL views let the planner push spatial predicates down to
-- each table's GiST index.
CREATE VIEW utility_features AS
    SELECT id, feature_type, quality_level, horizontal_accuracy, vertical_accuracy,
           geometry_type, material, utility_type, owner, nominal_diameter_mm,
           source_srid, attributes, created_at, geom::geometry(GeometryZ, 4326) AS geom
      FROM utility_points
    UNION ALL
    SELECT id, feature_type, quality_level, horizontal_accuracy, vertical_accuracy,
           geometry_type, material, utility_type, owner, nominal_diameter_mm,
           source_srid, attributes, created_at, geom::geometry(GeometryZ, 4326)
      FROM utility_segments
    UNION ALL
    SELECT id, feature_type, quality_level, horizontal_accuracy, vertical_accuracy,
           geometry_type, material, utility_type, owner, nominal_diameter_mm,
           source_srid, attributes, created_at, geom::geometry(GeometryZ, 4326)
      FROM utility_areas
    UNION ALL
    SELECT id, feature_type, quality_level, horizontal_accuracy, vertical_accuracy,
           geometry_type, material, utility_type, owner, nominal_diameter_mm,
           source_srid, attributes, created_at, geom::geometry(GeometryZ, 4326)
      FROM utility_objects;

-- ---------------------------------------------------------------------------
-- 811 dig tickets
-- ---------------------------------------------------------------------------
CREATE TABLE ticket_polygons (
    ticket_id             text PRIMARY KEY CHECK (btrim(ticket_id) <> ''),
    state_code            char(2) NOT NULL CHECK (state_code ~ '^[A-Z]{2}$'),
    -- Excavation footprint. 2D: the vertical extent is described by the columns below.
    geom                  geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(geom)),
    ground_elevation_m    double precision NOT NULL DEFAULT 0,
    excavation_depth_m    double precision NOT NULL DEFAULT 10
        CHECK (excavation_depth_m > 0 AND excavation_depth_m <= 100),
    tolerance_in_override numeric(4, 1) CHECK (tolerance_in_override BETWEEN 18 AND 24),
    work_type             text,
    excavator             text,
    source_srid           integer NOT NULL,
    raw_ticket            jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ticket_polygons_geom_gist ON ticket_polygons USING GIST (geom);

-- ---------------------------------------------------------------------------
-- GeoJSON helper: ST_AsGeoJSON does not encode PolyhedralSurface, so 3D objects are
-- emitted as a MultiPolygonZ of their faces. Coordinates rounded to 9 decimals
-- (~0.1 mm) to keep payloads small without losing survey precision.
-- ---------------------------------------------------------------------------
CREATE FUNCTION sue_geojson_geometry(g geometry) RETURNS json
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT ST_AsGeoJSON(
        CASE WHEN GeometryType(g) = 'POLYHEDRALSURFACE'
             THEN (SELECT ST_Collect(d.geom ORDER BY d.path) FROM ST_Dump(g) AS d)
             ELSE g END,
        9)::json
$$;

COMMIT;
