-- Runs once on first container start.
-- Creates the extensions and the two roles the RLS model depends on:
--   docflow       - the application role. RLS is FORCEd against it.
--   postgres      - owner/migration role, implicitly BYPASSRLS as superuser.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docflow') THEN
    CREATE ROLE docflow LOGIN PASSWORD 'docflow';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE docflow TO docflow;
GRANT USAGE, CREATE ON SCHEMA public TO docflow;

-- Anything the migration role creates later is usable by the app role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO docflow;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO docflow;

-- NOTE: uuidv7() is created by migration 0000_bootstrap, not here, so that a
-- managed Postgres (which never runs this file) gets it too.
