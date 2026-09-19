# PostgreSQL Strategy: one PG18 instance, three projects

**Verdict: yes.** One PostgreSQL 18 instance can carry both `vector` and `timescaledb`. A public image already ships both. Use one instance with three separate databases.

Method: I read official docs and the upstream Dockerfile (sources below) and inspected the three repos. I did **not** pull the image or run `CREATE EXTENSION` for both extensions, so item 1 is verified from the build source, not from a running container. Step 1 of the migration plan is the runtime check.

## 1. Does a public image ship both on pg18?

**Yes: `timescale/timescaledb-ha:pg18`.**

- The image's Dockerfile installs pgvector from apt (`postgresql-${pg}-pgvector`) for every PG version. It sets `shared_preload_libraries = 'timescaledb,...'` by default. Source: https://raw.githubusercontent.com/timescale/timescaledb-docker-ha/master/Dockerfile
- TimescaleDB 2.23.0 and later are marked `pg-max: 18` in that repo's `build_scripts/versions.yaml`. Source: https://raw.githubusercontent.com/timescale/timescaledb-docker-ha/master/build_scripts/versions.yaml
- The Docker Hub API lists these pg18 tags (queried 2026-09-19):
  - `pg18`
  - `pg18-all`
  - `pg18-ts2.30`
  - `pg18-ts2.30-all`
  - `pg18.6-ts2.30.1`
  - `pg18.6-ts2.30.1-all`
  - The `-oss` variants (e.g. `pg18-all-oss`) use the Apache-licensed TimescaleDB build. Avoid them, because Timescale's community features are excluded.
- Tag naming is documented at https://github.com/timescale/timescaledb-docker-ha. `-all` means the image also carries older PG major versions, so it is larger. Prefer a plain `pg18.6-ts2.30.1` pin.

**Caveats:**
- The Timescale install page (https://www.tigerdata.com/docs/self-hosted/latest/install/installation-docker) documents only timescaledb and toolkit for this image. It does not mention pgvector, so the pgvector claim rests on the Dockerfile, not the docs.
- The `-ha` image is Ubuntu-based, not Alpine, and includes Patroni and PostGIS, so it is heavier than `timescaledb:latest-pg18`.
- The `timescale/timescaledb:latest-pg18` image that Telemetry_System uses is documented as the light Alpine variant. The docs list no pgvector for it, and I found no evidence that it has pgvector.
- The `pgvector/pgvector:pg18` image does not ship timescaledb.

## 2. If not using `-ha`: custom image cost and conflicts

**Custom image cost.**
- Base on `timescale/timescaledb:latest-pg18` and build pgvector from source. pgvector documents `make && make install` (https://github.com/pgvector/pgvector). This needs a build stage with `git`, `make`, `gcc`, and the postgres dev headers.
- It is about a 10-line multi-stage Dockerfile. The ongoing cost is that you now own rebuilds for every pgvector or Postgres bump.
- Because `-ha` already includes both, a custom image is not worth it unless you insist on Alpine.

**Documented conflicts: none found.**
- The pgvector README makes no reference to TimescaleDB and says nothing about `shared_preload_libraries`, so it does not need preloading.
- TimescaleDB requires preloading: `shared_preload_libraries = 'timescaledb'`. The docs add "If you use other preloaded libraries, make sure they are comma separated." (https://www.tigerdata.com/docs/self-hosted/latest/install/installation-docker)
- The two extensions do not interact, so preloading timescaledb does not interfere with pgvector. In `-ha`, timescaledb is already preloaded (see Dockerfile above).
- One side effect is that a preloaded TimescaleDB starts background workers at server start. Timescale's docs describe them, and I did not measure their memory cost here.
- One documented behavior of TimescaleDB is that it must be `CREATE EXTENSION`'d as the first command in each database that uses it. I did not verify this in this session, so check it in step 2 of the plan.
- Absence of a documented conflict is not proof of compatibility. Step 1 of the migration plan tests it.

## 3. One instance with 3 databases, or 3 instances?

**Facts from the PostgreSQL 18 docs:**
- `shared_buffers` defaults to 128 MB, and the recommendation is 25% of RAM on a dedicated server. It is set once per server, not per database. Source: https://www.postgresql.org/docs/18/runtime-config-resource.html
- `work_mem` (default 4 MB) is per query operation, so total use is "many times the `work_mem` value" under concurrency. `maintenance_work_mem` defaults to 64 MB, and autovacuum can use it times `autovacuum_max_workers`. Same page.
- Roles are cluster-wide, connections cannot span databases, and extensions are created per database. Docs recommend separate databases for unrelated projects. Source: https://www.postgresql.org/docs/18/manage-ag-overview.html

**Implications:**

| | One instance, 3 DBs | Three instances |
|---|---|---|
| `shared_buffers` | One pool shared across all three DBs (e.g. 256 MB) | One per instance (3 × 128 MB at the default = 384 MB before anything else) |
| Fixed process overhead | One postmaster, one set of background workers (autovacuum launcher, checkpointer, WAL writer, etc.) | Triplicated |
| Per-connection memory | Same either way, driven by total connections | Same, but each instance needs its own headroom |
| Isolation | Shared CPU/IO; a runaway query in one DB slows the others. Roles are shared, so use a separate role per DB. | Strong |
| Extension blast radius | `timescaledb` preloaded server-wide, but `CREATE EXTENSION` is only in the telemetry DB | None |
| Upgrades/restarts | One restart affects all three apps | Independent |

**Recommendation:** one instance, three databases (`fragrance_db`, `rag_search_db`, `telemetry_db`). Memory is the dominant hosting cost, and this configuration pays for the buffer pool and the background processes once. I could not measure actual idle RSS here, so treat the savings as structural, not benchmarked. Give each app its own role with `CONNECT` only on its own database.

Use separate instances only if you need independent restarts or want to stop the timescaledb preload from affecting fragrance-api and rag-search-api.

## 4. What each repo currently assumes and what must change

| Repo | Current assumption | Change needed |
|---|---|---|
| **rag-search-api** (`docker-compose.yml`) | Image `pgvector/pgvector:pg18`. DB `rag_search_db`, user `postgres`. Host port `127.0.0.1:5433`. Volume `postgres_data:/var/lib/postgresql`. `init.sql` mounted into `/docker-entrypoint-initdb.d` runs `CREATE EXTENSION IF NOT EXISTS vector;`. `DATABASE_URL` points at service `db`. | Stop running its own `db` service. Point `DATABASE_URL` at the shared host and `rag_search_db`. `CREATE EXTENSION vector` must run in that database (see below). |
| **Telemetry_System** (`telemetry-system/docker-compose.yml`) | Image `timescale/timescaledb:latest-pg18`. DB `telemetry_db`. Port `5432:5432`. Volume `timescale_data:/var/lib/postgresql/data`. No init script. `create_tables.py` runs `create_all` only. Per its `CLAUDE.md`, the hypertable is a manual step: `SELECT create_hypertable('readings', 'recorded_at');`. The extension is created by neither compose nor code (grep found no `CREATE EXTENSION`). | Same removal of its own `db` service. Add `CREATE EXTENSION IF NOT EXISTS timescaledb;` to init, and automate or document the `create_hypertable` call. **Its `/var/lib/postgresql/data` mount is the wrong path for PG18** (see below). |
| **fragrance-api** (`core/settings.py:137-149`) | Plain Postgres. Uses `DATABASE_URL` if set, else `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`. Deployed on Railway per `DEPLOY.md`. No extensions. | Only its env values change: host and database name. No code change. |

**Init-script gotchas** (official `postgres` image docs, https://hub.docker.com/_/postgres):
- Scripts in `/docker-entrypoint-initdb.d` run **only when the data directory is empty**, and only against the `POSTGRES_DB` database. The current `init.sql` therefore only reaches one database on first boot.
- For the shared instance, write a single init script that runs `CREATE DATABASE` for each of the three databases and then `\connect`s to each one to create the right extension. Set `POSTGRES_DB` to one of them.
- If the volume already has data, re-running the init script does nothing. Run the statements by hand with `psql`, or wipe the volume.
- PG18 image change: `PGDATA` defaults to `/var/lib/postgresql/18/docker`, and the volume should be mounted at `/var/lib/postgresql`, not `/var/lib/postgresql/data`. rag-search-api already does this (commit `1963d4b`). Telemetry_System does not.
- Existing data will not carry over. The rag-search-api and telemetry-system volumes need a `pg_dump` and restore into the new instance.

**Suggested shared compose** (a sketch, not applied):
```yaml
services:
  db:
    image: timescale/timescaledb-ha:pg18.6-ts2.30.1   # pin; -ha includes pgvector
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: postgres
    volumes:
      - pg_data:/var/lib/postgresql   # NOTE: verify the -ha image's PGDATA layout
      - ./init-shared.sql:/docker-entrypoint-initdb.d/init.sql
```
- The `-ha` image is built by a different project from the official `postgres` image. Its PGDATA path, entrypoint, and user (`postgres` uid) differ. I did not verify that it honors `/docker-entrypoint-initdb.d`. Confirm this before relying on the sketch.

## Migration plan (proposed order)

1. Pull `timescale/timescaledb-ha:pg18`, start it, and run `CREATE EXTENSION vector;` and `CREATE EXTENSION timescaledb;` in separate scratch databases. This step is the runtime confirmation that the claim in item 1 is not just source-derived.
2. Check `SHOW shared_preload_libraries;` and measure idle memory.
3. Write `init-shared.sql`, dump and restore existing data, and repoint each app's `DATABASE_URL`.

No application code was changed. This file is the only thing written.
