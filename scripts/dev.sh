#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"
LOCAL_DIR="${AUTOLEADGEN_LOCAL_DIR:-${ROOT_DIR}/.local/dev}"
LOG_DIR="${LOCAL_DIR}/logs"
PYTHON_BIN="${AUTOLEADGEN_PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
BACKEND_PORT="${AUTOLEADGEN_BACKEND_PORT:-8001}"
FRONTEND_PORT="${AUTOLEADGEN_FRONTEND_PORT:-3000}"
FRONTEND_MODE="${AUTOLEADGEN_FRONTEND_MODE:-development}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
PASSWORD_FILE="${LOCAL_DIR}/acceptance-password"

usage() {
  cat <<'EOF'
Usage: ./scripts/dev.sh <command>

Commands:
  setup       Install Python and frontend dependencies.
  start       Prepare an isolated database and run API + frontend (default).
  check       Run backend and frontend verification.
  status      Probe the local API and frontend URLs.
  password    Print the generated isolated-local login password.
  help        Show this help.

Optional environment variables:
  AUTOLEADGEN_PYTHON_BIN      Python executable (default: .venv/bin/python)
  AUTOLEADGEN_LOCAL_DIR       Isolated runtime directory (default: .local/dev)
  AUTOLEADGEN_BACKEND_PORT    API port (default: 8001)
  AUTOLEADGEN_FRONTEND_PORT   frontend port (default: 3000)
  AUTOLEADGEN_FRONTEND_MODE   development or production (default: development)
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required."
}

require_runtime() {
  [[ -x "${PYTHON_BIN}" ]] || die "Python environment is missing. Run ./scripts/dev.sh setup first."
  "${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11 or newer is required."
  [[ -x "${FRONTEND_DIR}/node_modules/.bin/next" ]] \
    || die "Frontend dependencies are missing. Run ./scripts/dev.sh setup first."
  case "${FRONTEND_MODE}" in
    development) ;;
    production)
      [[ -f "${FRONTEND_DIR}/.next/BUILD_ID" ]] \
        || die "Production frontend build is missing. Run npm --prefix frontend run build first."
      ;;
    *) die "AUTOLEADGEN_FRONTEND_MODE must be development or production." ;;
  esac
}

ensure_secret() {
  local path="$1"
  umask 077
  mkdir -p "$(dirname "${path}")"
  if [[ ! -s "${path}" ]]; then
    openssl rand -base64 36 | tr -d '\r\n' > "${path}"
    chmod 600 "${path}"
  fi
}

configure_local_environment() {
  mkdir -p "${LOCAL_DIR}" "${LOG_DIR}"
  ensure_secret "${LOCAL_DIR}/jwt-secret"
  ensure_secret "${LOCAL_DIR}/smtp-encryption-key"
  ensure_secret "${LOCAL_DIR}/unsubscribe-token-secret"
  ensure_secret "${LOCAL_DIR}/webhook-secret"
  ensure_secret "${PASSWORD_FILE}"

  # Explicit values take precedence over the repository .env. Keeping every
  # connector fake makes this entrypoint safe even when .env contains real keys.
  export AUTOLEADGEN_ENV=local
  export AUTOLEADGEN_CONNECTOR_MODE=fake
  export ALLOW_REAL_EXTERNAL_CALLS=false
  export ALLOW_REAL_ACQUISITION_CALLS=false
  export OUTBOUND_HARD_PAUSE=true
  export PRODUCT_V2_ISOLATED_DATABASE=true
  export PRODUCT_V2_LEGACY_READ_ONLY=true
  export PRODUCT_V2_OWNER_PATH_ENFORCEMENT=false
  export PRODUCT_V2_LEGACY_WRITERS_FROZEN=false
  export PRODUCT_V2_LEGACY_WRITE_RECOVERY_APPROVED=false
  export ENABLE_BACKGROUND_WORKERS=false
  export ENABLE_PROSPECTING_WORKER=false
  export ENABLE_INBOX_MONITOR_WORKER=false
  export HEALTH_REQUIRE_ALEMBIC_HEAD=true
  export DATABASE_URL="sqlite+pysqlite:///${LOCAL_DIR}/autoleadgen.db"
  export JWT_SECRET_KEY="$(<"${LOCAL_DIR}/jwt-secret")"
  export SMTP_ENCRYPTION_KEY="$(<"${LOCAL_DIR}/smtp-encryption-key")"
  export UNSUBSCRIBE_TOKEN_SECRET="$(<"${LOCAL_DIR}/unsubscribe-token-secret")"
  export PRODUCT_V2_WEBHOOK_SECRET="$(<"${LOCAL_DIR}/webhook-secret")"
  export LOCAL_ACCEPTANCE_USERNAME=acceptance-admin
  export LOCAL_ACCEPTANCE_PASSWORD_FILE="${PASSWORD_FILE}"
  export BACKEND_URL
  export NEXT_PUBLIC_API_BASE_URL=""

  # A checked-in or shell-level *_FILE value must never conflict with the
  # isolated direct values above.
  unset DATABASE_URL_FILE JWT_SECRET_KEY_FILE SMTP_ENCRYPTION_KEY_FILE
  unset UNSUBSCRIBE_TOKEN_SECRET_FILE PRODUCT_V2_WEBHOOK_SECRET_FILE
}

assert_port_available() {
  local port="$1"
  local label="$2"
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    die "${label} port ${port} is already in use. Run ./scripts/dev.sh status or choose another port."
  fi
}

wait_for_url() {
  local url="$1"
  local pid="$2"
  local label="$3"
  local attempt
  for ((attempt = 1; attempt <= 60; attempt += 1)); do
    kill -0 "${pid}" >/dev/null 2>&1 || return 1
    if curl --fail --silent --show-error --max-time 2 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "${label} did not become ready: ${url}" >&2
  return 1
}

show_failure_logs() {
  local label="$1"
  local path="$2"
  echo "${label} log (${path}):" >&2
  tail -n 80 "${path}" 2>/dev/null || true
}

setup() {
  require_command python3
  require_command npm
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    [[ -z "${AUTOLEADGEN_PYTHON_BIN:-}" ]] \
      || die "AUTOLEADGEN_PYTHON_BIN is not executable: ${PYTHON_BIN}"
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
      || die "python3 must be Python 3.11 or newer."
    python3 -m venv "${ROOT_DIR}/.venv"
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
  fi
  "${PYTHON_BIN}" -m pip install --requirement "${ROOT_DIR}/requirements-dev.txt"
  npm --prefix "${FRONTEND_DIR}" ci
  echo "Dependencies are ready. Start the app with ./run.sh"
}

prepare_database() {
  configure_local_environment
  (
    cd "${ROOT_DIR}"
    "${PYTHON_BIN}" -m alembic upgrade head
    "${PYTHON_BIN}" scripts/bootstrap_local_acceptance.py
  )
}

start() {
  require_command curl
  require_command openssl
  require_runtime
  assert_port_available "${BACKEND_PORT}" "Backend"
  assert_port_available "${FRONTEND_PORT}" "Frontend"
  prepare_database

  local backend_log="${LOG_DIR}/backend.log"
  local frontend_log="${LOG_DIR}/frontend.log"
  local backend_pid=""
  local frontend_pid=""

  cleanup() {
    trap - EXIT INT TERM
    [[ -z "${frontend_pid}" ]] || kill "${frontend_pid}" >/dev/null 2>&1 || true
    [[ -z "${backend_pid}" ]] || kill "${backend_pid}" >/dev/null 2>&1 || true
    [[ -z "${frontend_pid}" ]] || wait "${frontend_pid}" 2>/dev/null || true
    [[ -z "${backend_pid}" ]] || wait "${backend_pid}" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  (
    cd "${ROOT_DIR}"
    if [[ "${FRONTEND_MODE}" == "development" ]]; then
      exec "${PYTHON_BIN}" -m uvicorn main:app --reload --host 127.0.0.1 --port "${BACKEND_PORT}"
    fi
    exec "${PYTHON_BIN}" -m uvicorn main:app --host 127.0.0.1 --port "${BACKEND_PORT}"
  ) >"${backend_log}" 2>&1 &
  backend_pid=$!

  (
    cd "${FRONTEND_DIR}"
    if [[ "${FRONTEND_MODE}" == "production" ]]; then
      [[ ! -e .next/standalone/public || -L .next/standalone/public ]] \
        || die ".next/standalone/public must be absent or a symlink."
      [[ ! -e .next/standalone/.next/static || -L .next/standalone/.next/static ]] \
        || die ".next/standalone/.next/static must be absent or a symlink."
      ln -sfn "${FRONTEND_DIR}/public" .next/standalone/public
      ln -sfn "${FRONTEND_DIR}/.next/static" .next/standalone/.next/static
      export HOSTNAME=127.0.0.1
      export PORT="${FRONTEND_PORT}"
      exec node .next/standalone/server.js
    fi
    exec node_modules/.bin/next dev --hostname 127.0.0.1 --port "${FRONTEND_PORT}"
  ) >"${frontend_log}" 2>&1 &
  frontend_pid=$!

  if ! wait_for_url "${BACKEND_URL}/health/ready" "${backend_pid}" "Backend"; then
    show_failure_logs "Backend" "${backend_log}"
    exit 1
  fi
  if ! wait_for_url "${FRONTEND_URL}/login" "${frontend_pid}" "Frontend"; then
    show_failure_logs "Frontend" "${frontend_log}"
    exit 1
  fi

  echo
  echo "AutoLeadGen is ready: ${FRONTEND_URL}"
  echo "API readiness: ${BACKEND_URL}/health/ready"
  echo "Username: acceptance-admin"
  echo "Password: ./scripts/dev.sh password"
  echo "Logs: ${LOG_DIR}"
  echo "Press Ctrl-C to stop both services."

  while kill -0 "${backend_pid}" >/dev/null 2>&1 \
    && kill -0 "${frontend_pid}" >/dev/null 2>&1; do
    sleep 1
  done

  if ! kill -0 "${backend_pid}" >/dev/null 2>&1; then
    show_failure_logs "Backend" "${backend_log}"
  fi
  if ! kill -0 "${frontend_pid}" >/dev/null 2>&1; then
    show_failure_logs "Frontend" "${frontend_log}"
  fi
  exit 1
}

check() {
  require_runtime
  (
    cd "${ROOT_DIR}"
    "${PYTHON_BIN}" -m pytest -q
    "${PYTHON_BIN}" scripts/export_openapi.py --check
  )
  npm --prefix "${FRONTEND_DIR}" run check:api-types
  npm --prefix "${FRONTEND_DIR}" test
  npm --prefix "${FRONTEND_DIR}" run lint
  npm --prefix "${FRONTEND_DIR}" run build
}

status() {
  local failed=0
  if curl --fail --silent --show-error --max-time 3 "${BACKEND_URL}/health/ready"; then
    echo
  else
    echo "Backend unavailable: ${BACKEND_URL}" >&2
    failed=1
  fi
  if curl --fail --silent --show-error --max-time 3 "${FRONTEND_URL}/login" >/dev/null; then
    echo "Frontend ready: ${FRONTEND_URL}"
  else
    echo "Frontend unavailable: ${FRONTEND_URL}" >&2
    failed=1
  fi
  return "${failed}"
}

password() {
  [[ -s "${PASSWORD_FILE}" ]] || die "No local password exists yet. Start the app once with ./run.sh"
  echo "Username: acceptance-admin"
  printf 'Password: '
  tr -d '\r\n' < "${PASSWORD_FILE}"
  echo
}

command="${1:-start}"
case "${command}" in
  setup) setup ;;
  start) start ;;
  check) check ;;
  status) status ;;
  password) password ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
