#!/usr/bin/env bash
# Phase 28.7 -- whole-cluster backup: PostgreSQL (full data) + MinIO objects.
#
# The backup lands in a directory on the RUNNER (outside any kind cluster),
# so destroying the cluster cannot destroy the backup. Every step is
# fail-closed: a failed dump or mirror aborts before the manifest is written.
#
# Usage: backup_cluster.sh <BACKUP_DIR>
# Requires: kubectl (context = Cluster A), mc on PATH, port-forward capable.
set -euo pipefail

BACKUP_DIR="$1"
PG_NS="cap-infra"
MINIO_LOCAL_PORT="19000"
CRED_FILE="$BACKUP_DIR/.restore-credentials"

mkdir -p "$BACKUP_DIR/postgres" "$BACKUP_DIR/objects"

echo "=== [1/4] PostgreSQL full dump (schema AND data) ==="
PG_POD=$(kubectl -n "$PG_NS" get pods -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$PG_NS" wait "pod/$PG_POD" --for=condition=Ready --timeout=300s
kubectl -n "$PG_NS" exec "$PG_POD" -- \
  pg_dump -U cap -d cap > "$BACKUP_DIR/postgres/cap.sql"
gzip -f "$BACKUP_DIR/postgres/cap.sql"
PG_DUMP="$BACKUP_DIR/postgres/cap.sql.gz"
[ -s "$PG_DUMP" ] || { echo "FATAL: empty pg_dump"; exit 1; }
grep -q "acquisition_runs" "$PG_DUMP" || { echo "FATAL: dump missing core tables"; exit 1; }

echo "=== [2/4] Schema revision ==="
SCHEMA_REV=$(kubectl -n "$PG_NS" exec "$PG_POD" -- \
  psql -U cap -d cap -tAc "SELECT version_num FROM alembic_version LIMIT 1")
echo "schema_revision=$SCHEMA_REV"

echo "=== [3/4] Object store mirror (mc, object-level export) ==="
kubectl -n "$PG_NS" port-forward svc/minio "${MINIO_LOCAL_PORT}:9000" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
for i in $(seq 1 30); do
  if (exec 3<>/dev/tcp/127.0.0.1/"$MINIO_LOCAL_PORT") 2>/dev/null; then exec 3>&-; break; fi
  sleep 1
done
(exec 3<>/dev/tcp/127.0.0.1/"$MINIO_LOCAL_PORT") 2>/dev/null \
  || { echo "FATAL: minio port-forward never became reachable"; exit 1; }
mc alias set drsource "http://127.0.0.1:${MINIO_LOCAL_PORT}" \
  "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mirror --overwrite "drsource/cap-evidence" "$BACKUP_DIR/objects/"
kill $PF_PID 2>/dev/null || true
wait $PF_PID 2>/dev/null || true
trap - EXIT

OBJ_COUNT=$(find "$BACKUP_DIR/objects" -type f | wc -l)
[ "$OBJ_COUNT" -gt 0 ] || { echo "FATAL: object mirror produced 0 files"; exit 1; }

echo "=== [4/4] Credentials for the restore side (never in the manifest) ==="
cat > "$CRED_FILE" <<EOF
MINIO_ROOT_USER=$MINIO_ROOT_USER
MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD
EOF

echo "BACKUP_DIR=$BACKUP_DIR"
echo "SCHEMA_REV=$SCHEMA_REV"
