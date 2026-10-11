-- Runs once, on first start of an empty data volume (docker-entrypoint-initdb.d).
-- One database per service so schemas never collide; the compose file gives each service its URL.
CREATE DATABASE hub;
CREATE DATABASE ledger;
CREATE DATABASE graph;
CREATE DATABASE geo;
CREATE DATABASE citizen;
