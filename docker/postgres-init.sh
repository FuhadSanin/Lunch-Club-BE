#!/bin/sh
# First start of the compose database: the app's own role, which must not be a superuser
# or row-level security is skipped. Values come from .env.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE ROLE "$DB_USER" LOGIN PASSWORD '$DB_PASSWORD' NOSUPERUSER CREATEDB;
CREATE DATABASE "$DB_NAME" OWNER "$DB_USER";
SQL
