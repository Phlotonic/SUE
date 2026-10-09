-- 01_extensions.sql — PostGIS 3.4 on PostgreSQL 16.
CREATE EXTENSION IF NOT EXISTS postgis;
-- SFCGAL provides ST_Extrude (excavation prism construction for 3D clash detection).
CREATE EXTENSION IF NOT EXISTS postgis_sfcgal;
