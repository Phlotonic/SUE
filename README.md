# SUE Geomatics Cloud: 811 Ticket Automation & ASCE 38-22 / 75-22 Platform

A cloud-native SaaS platform for Subsurface Utility Engineering (SUE) mapping, automated 811 excavation ticket screening, and ASCE 38-22 / ASCE 75-22 compliance verification.

---

## Architecture Overview

```
                      ┌──────────────────────────────────────────────┐
                      │    React 18 + TypeScript MapView (WebGL2)   │
                      │    MapLibre GL JS v4 + deck.gl v9            │
                      │    (Interleaved Context & Depth Occlusion)   │
                      └──────────────────────┬───────────────────────┘
                                             │ GeoJSON / REST
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │         FastAPI Backend (Python 3.12)        │
                      │   - Pydantic V2 (Rust Core Optimization)     │
                      │   - Module-Level Global TypeAdapters         │
                      │   - Zero-Copy TypedDict Serialization        │
                      └──────────────┬────────────────┬──────────────┘
                                     │                │
            Dynamic CRS Pipeline     │                │ 3D Clash & Tolerance Zone Engine
    ┌────────────────────────────────┴──┐   ┌─────────┴───────────────────────────────────┐
    │          pyproj 3.8               │   │           Collision Engine                  │
    │  - SPCS83 -> WGS84 Ingest         │   │  - Dynamic 18-to-24 in Tolerance Zones      │
    │  - WGS84 -> SPCS83 Export         │   │  - ASCE 38-22 Vertical Reliability Gating   │
    │  - EPSG Guidance Note 7-2 Verif.  │   │  - Vacuum Excavation Clearance Emulation    │
    └───────────────────────────────────┘   └─────────┬───────────────────────────────────┘
                                                      │ PostGIS Spatial DDL
                                                      ▼
                      ┌──────────────────────────────────────────────┐
                      │          PostgreSQL 16 + PostGIS 3.4         │
                      │  - Geometry(PointZ / LineStringZ / etc.)     │
                      │  - R-Tree Indexing: USING GIST (geom)        │
                      │  - ST_3DDWithin & ST_Extrude 3D Prisms       │
                      └──────────────────────────────────────────────┘
```

---

## Key Architectural Invariants

### 1. Coordinate Reference Systems (CRS)
* **Internal Storage:** All incoming geometries are immediately transformed to **WGS84 (EPSG:4326)** with explicit 3D Z coordinates in meters.
* **Export Pipeline:** Export dynamically projects geometries to the requested **NAD83 State Plane Coordinate System (SPCS83)** (e.g., Texas Central ftUS EPSG:6578, California zone 5 EPSG:2229) with verified sub-millimeter projection accuracy.
* **Geodetic Integrity:** Characterizes and accounts for PROJ null-shifts between NAD83(2011) and WGS84 (2.0m catalogued uncertainty), while projection inverse math from State Plane grid coordinates to geographic coordinates is verified to sub-millimeter precision against EPSG Guidance Note 7-2 benchmarks.

### 2. High-Performance Serialization (Pydantic V2 + Rust Core)
* All backend APIs returning GeoJSON utilize `TypedDict` and module-level `TypeAdapter` singletons initialized at import time.
* Eliminates dynamic Pydantic model constructor overhead on hot paths.
* Wrap validators are strictly prohibited to maintain raw Rust deserialization speed.

### 3. Dynamic 18-to-24 Inch Tolerance Zones & 3D Clash Mathematics
* Statutory tolerance zones are dynamically resolved by state jurisdiction:
  * **Texas (Tex. Util. Code ch. 251):** 18 inches + half nominal diameter.
  * **California (Cal. Gov. Code 4216):** 24 inches on each side of marking.
  * **National Default:** 24 inches (conservative safety envelope).
* **ASCE 38-22 Vertical Reliability Gating:**
  * **Quality Level A (QL-A):** Physically exposed (vacuum excavation) and surveyed (horizontal $\le 0.06\text{ m}$, vertical $\le 0.03\text{ m}$). Evaluated against the full 3D extruded excavation prism.
  * **Quality Levels B, C, D:** Vertical position is unverified by physical exposure. Per ASCE 38-22 principles, unverified Z coordinates are **never permitted to clear a clash**; any horizontal overlap within the excavation zone + tolerance buffer triggers a mandatory potholing / vacuum excavation alert.

### 4. Interleaved WebGL Rendering (deck.gl v9 + MapLibre GL JS v4)
* Standard map overlay modes fail mathematically when rendering subterranean utility lines due to lack of depth buffer sharing.
* Utilizes `MapboxOverlay` from `@deck.gl/maplibre` with `interleaved: true` to inject deck.gl layers directly into MapLibre's WebGL2 rendering pipeline, enabling authentic 3D depth occlusion between base maps, terrain, utility lines, and extruded 811 excavation prisms.

---

## Directory Structure

```
.
├── .agents/
│   ├── hooks.json                     # Lifecycle safety hooks
│   ├── rules/
│   │   └── asce-compliance.md         # ASCE 75-22 / 38-22 glob-activated rule
│   ├── skills/
│   │   └── deckgl-interleaved/
│   │       └── SKILL.md               # Interleaved WebGL rendering skill
│   └── subagents/
│       └── geospatial-auditor.md      # Geospatial Auditor subagent manifest
├── backend/
│   ├── app/
│   │   ├── clash.py                   # 3D Collision detection & tolerance engine
│   │   ├── crs.py                     # Vectorized CRS transformation pipeline
│   │   ├── db.py                      # PostGIS 3.4 database connection layer
│   │   └── main.py                    # FastAPI application & REST endpoints
│   ├── models/
│   │   └── utility.py                 # ASCE 38-22 / 75-22 models & TypedDicts
│   ├── schemas/
│   │   └── clash.py                   # 811 Ticket schemas & tolerance lookup
│   ├── tests/
│   │   ├── test_api.py                # FastAPI integration tests
│   │   ├── test_clash.py              # 3D clash & tolerance zone tests
│   │   ├── test_crs.py                # EPSG GN 7-2 geodetic verification suite
│   │   └── test_models.py             # ASCE accuracy constraint tests
│   └── pyproject.toml                 # uv package configuration (Python 3.12)
├── db/
│   └── init/
│       ├── 01_extensions.sql          # postgis and postgis_sfcgal
│       └── 02_schema.sql              # 3D spatial schema with R-Tree GiST indexes
├── frontend/
│   ├── src/
│   │   ├── App.tsx                    # Main React application
│   │   ├── MapView.tsx                # Interleaved MapLibre + deck.gl component
│   │   └── main.tsx                   # Entrypoint
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── specs/
│   ├── plan.md                        # Implementation Plan
│   └── tasks.md                       # Task List
├── docker-compose.yml                 # PostGIS 16-3.4 container service
├── LICENSE.md                         # Business Source License 1.1 (BSL 1.1)
└── README.md
```

---

## Running the Platform

### Backend
```bash
cd backend
uv sync
uv run pytest -q
uv run uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Database (PostgreSQL 16 + PostGIS 3.4)
```bash
docker compose up -d db
```

---

## Licensing & Enterprise Monetization
This software is owned by **Adam Dryden** and licensed under the **Business Source License 1.1 (BSL 1.1)**:
* Free for development, non-production evaluation, research, and testing.
* Commercial production, hosted SaaS, and automated 811 ticket screening services require an Enterprise Commercial License directly from Adam Dryden (`adryden508@gmail.com`).
* Automatically transitions to Apache 2.0 on 2030-10-01.
