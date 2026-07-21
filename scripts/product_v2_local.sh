#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="${ROOT_DIR}/.local"
SECRETS_DIR="${LOCAL_DIR}/secrets"
COMPOSE_FILE="${ROOT_DIR}/compose.product-v2.yml"
PYTHON_BIN="${AUTOLEADGEN_PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

require_python() {
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Python environment is missing. Run ./scripts/dev.sh setup first." >&2
    exit 1
  fi
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required for the isolated MySQL 8 environment." >&2
    exit 1
  fi
  docker info >/dev/null 2>&1 || {
    echo "Docker is installed but the daemon is not running." >&2
    exit 1
  }
}

ensure_secrets() {
  umask 077
  mkdir -p "${SECRETS_DIR}"
  if [[ ! -s "${SECRETS_DIR}/mysql_password" ]]; then
    openssl rand -hex 24 > "${SECRETS_DIR}/mysql_password"
  fi
  if [[ ! -s "${SECRETS_DIR}/mysql_root_password" ]]; then
    openssl rand -hex 24 > "${SECRETS_DIR}/mysql_root_password"
  fi
}

database_url() {
  local password
  password="$(<"${SECRETS_DIR}/mysql_password")"
  printf 'mysql+pymysql://autoleadgen:%s@127.0.0.1:3307/autoleadgen_v2?charset=utf8mb4' "${password}"
}

wait_for_mysql() {
  local attempts=0
  until docker compose -f "${COMPOSE_FILE}" exec -T mysql mysqladmin ping -h 127.0.0.1 --silent >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if (( attempts >= 60 )); then
      echo "MySQL did not become ready within 120 seconds." >&2
      exit 1
    fi
    sleep 2
  done
}

run_alembic() {
  require_python
  AUTOLEADGEN_ENV=local \
  AUTOLEADGEN_CONNECTOR_MODE=fake \
  ALLOW_REAL_EXTERNAL_CALLS=false \
  DATABASE_URL="$(database_url)" \
    "${PYTHON_BIN}" -m alembic -c "${ROOT_DIR}/alembic.ini" upgrade head
}

ensure_test_database() {
  local test_database="autoleadgen_v2_test"
  if [[ ! "${test_database}" =~ ^autoleadgen_v2_[a-z0-9_]*test$ ]]; then
    echo "Refusing to recreate an unexpected test database: ${test_database}" >&2
    exit 1
  fi

  local sql
  sql="DROP DATABASE IF EXISTS \`${test_database}\`; CREATE DATABASE \`${test_database}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL PRIVILEGES ON \`${test_database}\`.* TO 'autoleadgen'@'%'; FLUSH PRIVILEGES;"
  docker compose -f "${COMPOSE_FILE}" exec -T mysql \
    sh -eu -c '
      config_file="$(mktemp)"
      trap "rm -f \"${config_file}\"" EXIT
      chmod 600 "${config_file}"
      {
        printf "%s\n" "[client]" "user=root"
        printf "password=%s\n" "$(cat /run/secrets/mysql_root_password)"
      } > "${config_file}"
      mysql --defaults-extra-file="${config_file}" -e "${1}"
    ' sh "${sql}"
}

test_database_url() {
  local password
  # The isolated migration-history test creates and drops a derived *_test
  # database. This root credential exists only inside the disposable local
  # Compose database and is never used by an application process.
  password="$(<"${SECRETS_DIR}/mysql_root_password")"
  printf 'mysql+pymysql://root:%s@127.0.0.1:3307/autoleadgen_v2_test?charset=utf8mb4' "${password}"
}

command="${1:-help}"
case "${command}" in
  init)
    ensure_secrets
    echo "Local Product V2 secrets initialized with owner-only permissions."
    ;;
  up)
    require_docker
    ensure_secrets
    docker compose -f "${COMPOSE_FILE}" up -d mysql
    wait_for_mysql
    run_alembic
    echo "Product V2 MySQL is ready on 127.0.0.1:3307."
    ;;
  migrate)
    require_docker
    ensure_secrets
    wait_for_mysql
    run_alembic
    ;;
  status)
    require_docker
    docker compose -f "${COMPOSE_FILE}" ps
    ;;
  test)
    require_docker
    require_python
    ensure_secrets
    wait_for_mysql
    ensure_test_database
    AUTOLEADGEN_ENV=test \
    AUTOLEADGEN_CONNECTOR_MODE=fake \
    ALLOW_REAL_EXTERNAL_CALLS=false \
    PRODUCT_V2_MYSQL_TEST_URL="$(test_database_url)" \
      "${PYTHON_BIN}" -m pytest -q -m mysql
    ;;
  down)
    require_docker
    docker compose -f "${COMPOSE_FILE}" down
    ;;
  destroy)
    require_docker
    docker compose -f "${COMPOSE_FILE}" down --volumes
    echo "The isolated Product V2 database volume was removed."
    ;;
  *)
    echo "Usage: $0 {init|up|migrate|status|test|down|destroy}" >&2
    exit 2
    ;;
esac
