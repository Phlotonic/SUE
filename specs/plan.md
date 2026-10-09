# Implementation Plan: 811 Ticket Automation & Clash Detection Engine

## 1. Overview
Develop a high-throughput backend service that ingests digital 811 dig tickets, parses their bounding geometric polygons, and performs real-time 3D clash detection against the ASCE 75-22 verified utility database.

## 2. Architecture Decisions
- **API Framework:** FastAPI with Pydantic V2 core.
- **Performance Optimization:**
  - Initialize `TypeAdapter` objects globally at module load rather than dynamically inside endpoint functions to eliminate redundant constructor overhead.
  - Utilize `TypedDict` over deeply nested model inheritance for GeoJSON output.
  - Rust core optimization (`pydantic-core`) mandatory.
- **Database:** PostgreSQL 16 extended with PostGIS 3.4 and SFCGAL (`postgis_sfcgal`).
- **Spatial Indexing:** Create an R-Tree-over-GiST index (`USING GIST (geom)`) on all spatial tables and views to enable logarithmic search traversal.
- **Coordinate Transformation:** Strict transformation to WGS84 (`EPSG:4326`, 3D Z in meters) on ingest; dynamic projection to requested NAD83 State Plane on export.
- **Tolerance Zones:** 3D collision detection programmatically enforcing dynamic 18-to-24 inch tolerance zone with vacuum excavation clearance emulation.

## 3. Data Models
### UtilityFeature (ASCE 75-22 Compliant)
```python
from typing import Annotated, Literal
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

class UtilityFeature(TypedDict):
    id: str
    feature_type: Literal['Segment', 'Device', 'AccessPoint']
    quality_level: Literal['QL-A', 'QL-B', 'QL-C', 'QL-D']
    horizontal_accuracy: float
    vertical_accuracy: float
    geometry_type: Literal['Point', 'LineString', 'Polygon', '3DObject']
    material: str
    utility_type: str
    geom: dict # GeoJSON 3D Geometry
```

## 4. Clash Detection Algorithm
To strictly enforce the statutory 18-to-24 inch (approximately 0.4572m to 0.6096m) tolerance zone surrounding existing utilities, the backend executes 3D clash detection combining 2D metric buffer projection with vertical extrusion and depth interval evaluation:

```sql
SELECT u.id, u.feature_type, u.quality_level, u.geometry_type, u.material, u.horizontal_accuracy, u.vertical_accuracy
FROM utility_features u
JOIN ticket_polygons t ON t.ticket_id = :ticket_id
WHERE u.geom && ST_Expand(t.geom, :deg_margin)
  AND ST_3DDWithin(
      ST_Transform(u.geom, :metric_srid),
      ST_Extrude(ST_Transform(t.geom, :metric_srid), 0, 0, -t.excavation_depth_m),
      :tolerance_m + u.horizontal_accuracy
  );
```
