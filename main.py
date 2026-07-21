import os
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from database import SessionLocal
import models as legacy

from routers import (
    auth, agent, analytics, api_usage, channels, client_pools, compliance,
    credits, crm_webhooks, email_accounts, email_logs, email_templates, health, leads, notifications, personas, replies, workflow_health, workflows
)
from product_v2.api import router as product_v2_router
from product_v2.migration_api import (
    authenticated_owner_id,
    legacy_write_path_allowed,
    require_v2_write_path,
    router as product_v2_migration_router,
)
from product_v2.migration_state import (
    OwnerMigrationConflict,
    owner_path_enforcement_enabled,
    serialize_owner_write_path,
)
from product_v2.settings_api import router as product_v2_settings_router
from product_v2.production import database_readiness_checks
from product_v2.metrics import observe_http, prometheus_metrics
from runtime_config import RuntimeConfigurationError, environment, is_production_like, read_flag

# Schema creation and workers are deliberately not part of the API process.
# Alembic owns schema changes; dedicated worker entrypoints own automation.
app = FastAPI(title="AutoLeadGen API", version="2.0.0")

app.middleware("http")(observe_http)


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _legacy_get_has_side_effects(request, path: str) -> bool:
    """Identify legacy GETs that write state or call real providers.

    The migration read-only boundary is about effects, not HTTP verb names.
    These legacy endpoints pre-date that boundary and either lazily persist
    data or probe paid/external providers when read.
    """
    truthy = {"1", "true", "yes", "on"}
    query_flag = lambda name: request.query_params.get(name, "").strip().lower() in truthy
    return (
        path == "/api/auth/users"
        or path.startswith("/api/credits/me")
        or path.startswith("/api/credits/transactions")
        or path.startswith("/api/credits/users/")
        or path == "/api/api-usage/summary"
        or (path == "/api/channels/accounts" and query_flag("sync"))
        or (path == "/api/health/status" and query_flag("external"))
        or (path.startswith("/api/workflows/") and path.endswith("/health"))
    )


@app.middleware("http")
async def enforce_legacy_read_only(request, call_next):
    """Keep legacy business APIs read-only during the local V2 cutover.

    Login and consent confirmation remain available. The guard is feature
    flagged so unchanged production deployments are not affected by local work.
    """
    path = request.url.path.rstrip("/") or "/"
    is_legacy_api = path.startswith("/api/") and not path.startswith("/api/v2/")
    writeful_legacy_get = _legacy_get_has_side_effects(request, path)
    is_safe_method = request.method in {"GET", "HEAD", "OPTIONS"} and not writeful_legacy_get
    is_required_exception = (
        path == "/api/auth/login"
        or path == "/api/auth/logout"
        or path.startswith("/api/unsubscribe/")
        or path == "/api/channels/webhooks/unipile"
    )
    is_write_request = is_legacy_api and not is_safe_method and not is_required_exception

    def blocked_response(code: str, message: str):
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": code,
                    "message": message,
                }
            },
        )

    if not is_write_request:
        return await call_next(request)

    # The explicit flag remains a deployment-wide emergency freeze.  It is
    # evaluated before authentication and preserves the previous local
    # Product V2 preview behavior.
    if _flag(
        "PRODUCT_V2_LEGACY_READ_ONLY",
        os.environ.get("AUTOLEADGEN_ENV", "").lower() == "local",
    ):
        return blocked_response(
            "LEGACY_API_READ_ONLY",
            "Legacy business APIs are read-only during the Product V2 cutover",
        )

    if owner_path_enforcement_enabled():
        owner_id = authenticated_owner_id(request)
        if owner_id is not None:
            # Hold the owner-scoped advisory fence through the legacy handler.
            # A concurrent path switch cannot pass the same fence until this
            # request has committed or failed, while the route's independent
            # ORM session remains free to reference or update the user row.
            fence_db = SessionLocal()
            try:
                with serialize_owner_write_path(
                    fence_db,
                    owner_id,
                    commit_on_success=True,
                ):
                    owner_exists = fence_db.query(legacy.User.id).filter(
                        legacy.User.id == owner_id,
                        legacy.User.is_active.is_(True),
                    ).first()
                    if owner_exists is None:
                        return await call_next(request)
                    allowed = legacy_write_path_allowed(fence_db, owner_id)
                    if not allowed:
                        return blocked_response(
                            "OWNER_LEGACY_WRITE_PATH_INACTIVE",
                            "This owner has activated the Product V2 write path",
                        )
                    return await call_next(request)
            except OwnerMigrationConflict:
                # Missing/inactive identities retain the route's normal 401.
                return await call_next(request)
            finally:
                fence_db.rollback()
                fence_db.close()
    return await call_next(request)

# Configure CORS
_cors_origins = []
if environment() in {"local", "test"}:
    _cors_origins.extend(
        [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
        ]
    )
# Allow additional origins from environment variable
_env_origins = os.environ.get("CORS_ORIGINS", "")
if _env_origins:
    _cors_origins.extend([o.strip() for o in _env_origins.split(",") if o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_allowed_hosts = [
    item.strip()
    for item in os.environ.get("ALLOWED_HOSTS", "").split(",")
    if item.strip()
]
if _allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# Mount Static Files (for any temporary media or downloads)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Include all API Routers
app.include_router(auth.router)
app.include_router(agent.router)
app.include_router(analytics.router)
app.include_router(api_usage.router)
app.include_router(channels.router)
app.include_router(client_pools.router)
app.include_router(compliance.router)
app.include_router(credits.router)
app.include_router(crm_webhooks.router)
app.include_router(email_accounts.router)
app.include_router(email_logs.router)
app.include_router(email_templates.router)
app.include_router(health.router)
app.include_router(leads.router)
app.include_router(notifications.router)
app.include_router(personas.router)
app.include_router(replies.router)
app.include_router(workflow_health.router)
app.include_router(workflows.router)
app.include_router(
    product_v2_router,
    dependencies=[Depends(require_v2_write_path)],
)
app.include_router(
    product_v2_settings_router,
    dependencies=[Depends(require_v2_write_path)],
)
app.include_router(product_v2_migration_router)

@app.get("/")
def read_root():
    return {"message": "AutoLeadGen API is running."}

@app.get("/health/live")
def liveness_check():
    return {
        "status": "live",
        "release": os.environ.get("RELEASE_SHA", "development"),
    }


@app.get("/metrics", include_in_schema=False)
def metrics():
    db = SessionLocal()
    try:
        return prometheus_metrics(db)
    finally:
        db.rollback()
        db.close()


def _readiness_response():
    db = SessionLocal()
    try:
        try:
            require_head = read_flag(
                "HEALTH_REQUIRE_ALEMBIC_HEAD",
                default=is_production_like(),
            )
        except RuntimeConfigurationError:
            require_head = True
        checks = database_readiness_checks(db, require_head=require_head)
    finally:
        db.rollback()
        db.close()
    ready = all(check.passed for check in checks)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "release": os.environ.get("RELEASE_SHA", "development"),
            "checks": [
                {"name": check.name, "passed": check.passed, "message": check.message}
                for check in checks
            ],
        },
    )


@app.get("/health/ready")
def readiness_check():
    return _readiness_response()


@app.get("/health")
def health_check():
    return _readiness_response()
