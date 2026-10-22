#!/bin/sh
# The Agreements database and its owner. Runs on the database server's first start only.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE tfsc LOGIN PASSWORD '${POSTGRES_PASSWORD}';
CREATE DATABASE tfsc OWNER tfsc;
SQL
