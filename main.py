import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import engine, Base
from routers import (
    auth, agent, analytics, channels, client_pools,
    email_accounts, email_logs, leads, personas, replies, workflows
)

# Initialize database tables
Base.metadata.create_all(bind=engine)

def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    import threading

    def run_in_new_loop(coro_func):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro_func())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in background thread loop: {e}")
        finally:
            loop.close()

    threads = []

    if _env_flag("ENABLE_BACKGROUND_WORKERS", False):
        from services.outbound_engine import run_outbound_loop
        t = threading.Thread(target=run_in_new_loop, args=(run_outbound_loop,), name="outbound-engine", daemon=True)
        t.start()
        threads.append(t)

    if _env_flag("ENABLE_PROSPECTING_WORKER", False):
        from services.outbound_engine import run_prospecting_loop
        t = threading.Thread(target=run_in_new_loop, args=(run_prospecting_loop,), name="prospecting-engine", daemon=True)
        t.start()
        threads.append(t)

    if _env_flag("ENABLE_INBOX_MONITOR_WORKER", False):
        from services.inbox_monitor import run_inbox_monitor_loop
        t = threading.Thread(target=run_in_new_loop, args=(run_inbox_monitor_loop,), name="inbox-monitor", daemon=True)
        t.start()
        threads.append(t)

    app.state.background_threads = threads
    try:
        yield
    finally:
        pass

app = FastAPI(title="AutoLeadGen API", lifespan=lifespan)

# Configure CORS
_cors_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://39.102.113.35",
    "http://39.102.113.35:3000",
    "http://39.102.113.35:3001",
    "http://39.102.113.35:8001",
    "http://39.102.113.35:8888",
]
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

# Mount Static Files (for any temporary media or downloads)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Include all API Routers
app.include_router(auth.router)
app.include_router(agent.router)
app.include_router(analytics.router)
app.include_router(channels.router)
app.include_router(client_pools.router)
app.include_router(email_accounts.router)
app.include_router(email_logs.router)
app.include_router(leads.router)
app.include_router(personas.router)
app.include_router(replies.router)
app.include_router(workflows.router)

@app.get("/")
def read_root():
    return {"message": "AutoLeadGen API is running."}

@app.get("/health")
def health_check():
    return {"status": "healthy"}
