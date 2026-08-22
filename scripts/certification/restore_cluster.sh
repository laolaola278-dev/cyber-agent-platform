#!/usr/bin/env bash
# Phase 28.7 -- restore into a FRESH cluster, fail-closed.
#
# Verifies the backup against its manifest BEFORE touching the target
# database: any digest mismatch aborts with nothing restored (no partial
# restore may ever report healthy).
#
# Usage: restore_cluster.sh <BACKUP_DIR> <PG_POD> <MINIO_LOCAL_PORT>
# Env:   MINIO_ROOT_USER / MINIO_ROOT_PASSWORD (target cluster credentials)
set -euo pipefail

BACKUP_DIR="$1"
PG_POD="$2"
MINIO_LOCAL_PORT="$3"

echo "=== [1/3] Verify backup integrity against manifest (fail-closed) ==="
python3 "$(dirname "$0")/verify_backup_manifest.py" "$BACKUP_DIR"

echo "=== [2/3] Restore PostgreSQL ==="
kubectl -n cap-infra exec "$PG_POD" -- sh -c \
  "psql -U postgres -c 'DROP DATABASE IF EXISTS cap;' \
       -c 'CREATE DATABASE cap OWNER cap;'" >/dev/null
gzip -dc "$BACKUP_DIR/postgres/cap.sql.gz" \
  | kubectl -n cap-infra exec -i "$PG_POD" -- psql -U cap -d cap -v ON_ERROR_STOP=1 -q 2>&1 | tail -5

ROWS=$(kubectl -n cap-infra exec "$PG_POD" -- \
  psql -U cap -d cap -tAc "SELECT count(*) FROM acquisition_runs")
echo "restored acquisition_runs rows=$ROWS"
[ "$ROWS" -gt 0 ] || { echo "FATAL: restore produced 0 runs"; exit 1; }

echo "=== [3/3] Restore object store ==="
kubectl -n cap-infra port-forward svc/minio "${MINIO_LOCAL_PORT}:9000" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
for i in $(seq 1 30); do
  if (exec 3<>/dev/tcp/127.0.0.1/"$MINIO_LOCAL_PORT") 2>/dev/null; then exec 3>&-; break; fi
  sleep 1
done
mc alias set drtarget "http://127.0.0.1:${MINIO_LOCAL_PORT}" \
  "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing drtarget/cap-evidence >/dev/null
mc mirror --overwrite "$BACKUP_DIR/objects/" "drtarget/cap-evidence/"
kill $PF_PID 2>/dev/null || true
wait $PF_PID 2>/dev/null || true
trap - EXIT

echo "RESTORE_OK rows=$ROWS objects=$(find "$BACKUP_DIR/objects" -type f | wc -l)"
