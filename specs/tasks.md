# Task List: Core Engine Implementation

- [x] **Task 1: Database Initialization**
  - [x] Provision PostgreSQL container configuration with PostGIS 3.4 (`docker-compose.yml`).
  - [x] Execute/define DDL scripts to create `utility_features` (with `utility_points`, `utility_segments`, `utility_areas`, `utility_objects`) and `ticket_polygons` tables.
  - [x] Validate creation of `USING GIST (geom)` R-Tree index on all `Geometry(*Z, 4326)` columns.
  - *Delegation: Primary Agent*

- [x] **Task 2: CRS Transformation Pipeline**
  - [x] Implement `pyproj.Transformer` utility module (`backend/app/crs.py`).
  - [x] Create unit tests validating sub-centimeter conversion accuracy from NAD83(2011) State Plane to WGS84 and EPSG Guidance Note 7-2 benchmarks (`backend/tests/test_crs.py`).
  - *Delegation: `geospatial-auditor` subagent (via `invoke_subagent`)*

- [x] **Task 3: Backend API Endpoints**
  - [x] Scaffold FastAPI application (`backend/app/main.py`).
  - [x] Implement Pydantic V2 `TypedDict` and model schemas conforming to ASCE 75-22 and ASCE 38-22 (`backend/models/utility.py`, `backend/schemas/clash.py`).
  - [x] Build the `/api/v1/clash-detect` endpoint wrapping the `ST_3DDWithin` logic with dynamic 18-to-24 inch tolerance zones.
  - [x] Implement high-performance GeoJSON serialization with global `TypeAdapter` instances.
  - *Delegation: Primary Agent*

- [x] **Task 4: Deck.gl Visualization Component**
  - [x] Scaffold React 18 TypeScript application (`frontend/`).
  - [x] Scaffold React component `MapView.tsx`.
  - [x] Import `MapboxOverlay` from `@deck.gl/maplibre`.
  - [x] Configure interleaved rendering context to share WebGL buffers (`interleaved: true`).
  - [x] Render clash results using `GeoJsonLayer` with depth occlusion.
  - *Delegation: Primary Agent*

- [x] **Task 5: Monetization & Enterprise Capitalization License**
  - [x] Choose and establish the optimal commercial SaaS license for maximal capitalization and access (Business Source License 1.1 directly assigned to Adam Dryden).
  - *Delegation: Primary Agent*
