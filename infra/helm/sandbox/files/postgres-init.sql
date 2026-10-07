-- One Postgres instance, one database and role per service.
CREATE ROLE orders LOGIN PASSWORD 'orders';
CREATE DATABASE orders OWNER orders;

CREATE ROLE inventory LOGIN PASSWORD 'inventory';
CREATE DATABASE inventory OWNER inventory;

-- Read-only monitoring role for postgres-exporter.
CREATE ROLE exporter LOGIN PASSWORD 'exporter';
GRANT pg_monitor TO exporter;
