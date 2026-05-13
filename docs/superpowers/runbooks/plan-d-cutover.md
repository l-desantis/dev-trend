# Plan D Cutover Runbook

Migration from SQLite to PostgreSQL on the VPS (46.225.112.216).

## Pre-flight checklist

- [ ] PR `feat/postgres-alembic` merged and CI green
- [ ] New image built and pushed to GHCR
- [ ] SSH access to VPS confirmed
- [ ] Docker and docker-compose available on VPS

## Step 1 — Export existing SQLite data (optional, for records)

If you want to keep the old SQLite data as a backup:

```bash
ssh vps "cd /opt/dev-trend && docker compose -f docker-compose.yml exec app sqlite3 /data/devtrend.db .dump > /tmp/sqlite-backup-$(date +%Y%m%d).sql"
scp vps:/tmp/sqlite-backup-*.sql ./backups/
```

> Skip this step if the old data is not needed in Postgres. The first run will start fresh.

## Step 2 — Update .env on VPS

Add/update these variables in `/opt/dev-trend/.env`:

```env
DATABASE_URL=postgresql+asyncpg://devtrend:devtrend@postgres:5432/devtrend
POSTGRES_USER=devtrend
POSTGRES_PASSWORD=<strong-password-here>
POSTGRES_DB=devtrend
```

Replace `<strong-password-here>` with a real password, and update `DATABASE_URL` to match.

## Step 3 — Pull new image and restart

```bash
ssh vps "cd /opt/dev-trend && \
  docker compose -f docker-compose.yml pull && \
  docker compose -f docker-compose.yml down && \
  docker compose -f docker-compose.yml up -d"
```

Compose will start `postgres`, wait for it to be healthy, run `migrate` (`alembic upgrade head`), then start `app`.

## Step 4 — Verify

```bash
# Migrations applied
ssh vps "docker exec dev-trend-postgres psql -U devtrend -d devtrend -c '\dt'"
# Should list: categories, source_items, pain_points, opportunity_candidates, etc.

# App health
curl https://<your-domain>/health
# Expected: {"status": "ok"}

# App logs (look for "database ready")
ssh vps "docker compose -f docker-compose.yml logs app --tail 50"
```

## Step 5 — Cleanup old SQLite volume (after confirming healthy)

```bash
ssh vps "docker volume rm devtrend_db 2>/dev/null || true"
```

## Rollback

If the new deploy fails:

1. Stop new containers: `docker compose -f docker-compose.yml down`
2. Check out the previous image tag and redeploy with the old `docker-compose.yml` (pre-Plan-D)
3. The old SQLite volume `devtrend_db` is still present until explicitly deleted

## Monitoring after cutover

Watch for errors in the first 30 minutes:

```bash
ssh vps "docker compose -f docker-compose.yml logs -f app"
```

Key log lines to confirm success:
- `database ready` — `check_db_reachable()` passed
- `backfill_connector_done` — ingestion running
- No `DBAPIError` or `asyncpg` exceptions
