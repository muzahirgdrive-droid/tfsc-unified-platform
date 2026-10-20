#!/bin/sh
# One PostgreSQL server, three databases: crm (owner crm), tfsc (Agreements), keycloak. Runs on first start only.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE tfsc LOGIN PASSWORD '${POSTGRES_PASSWORD}';
CREATE DATABASE tfsc OWNER tfsc;
CREATE ROLE keycloak LOGIN PASSWORD '${POSTGRES_PASSWORD}';
CREATE DATABASE keycloak OWNER keycloak;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL
