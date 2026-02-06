#!/usr/bin/env sh
set -eu

wait_for_service() {
  service_name="$1"
  probe_cmd="$2"
  timeout_seconds="${3:-60}"
  elapsed=0

  echo "Waiting for ${service_name}..."
  while ! sh -c "${probe_cmd}" >/dev/null 2>&1; do
    elapsed=$((elapsed + 2))
    if [ "${elapsed}" -ge "${timeout_seconds}" ]; then
      echo "Timed out waiting for ${service_name} after ${timeout_seconds}s"
      return 1
    fi
    sleep 2
  done
  echo "${service_name} is ready."
}

if [ "${WAIT_FOR_DEPENDENCIES:-true}" = "true" ]; then
  if [ -n "${REDIS_URL:-}" ]; then
    wait_for_service \
      "redis" \
      "python -c \"import redis, os; redis.from_url(os.environ['REDIS_URL']).ping()\"" \
      "${WAIT_FOR_REDIS_TIMEOUT_SECONDS:-90}"
  fi

  if [ -n "${DATABASE_URL:-}" ]; then
    wait_for_service \
      "postgres" \
      "python -c \"import os, psycopg2; conn = psycopg2.connect(os.environ['DATABASE_URL']); conn.close()\"" \
      "${WAIT_FOR_DB_TIMEOUT_SECONDS:-90}"
  fi
fi

exec "$@"
