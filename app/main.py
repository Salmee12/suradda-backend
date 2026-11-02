import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1.api import api_router
from app.api.internal import router as internal_router
from app.api.admin import router as admin_router
from app.db.session import AsyncSessionLocal
from app.services.room_cleanup import sweep_idle_rooms, ROOM_IDLE_MINUTES

logger = logging.getLogger(__name__)

# How often the safety-net sweeper wakes up.
_SWEEP_INTERVAL_SECONDS = 5 * 60  # 5 minutes


async def _room_sweeper_loop():
    """Periodic safety net: close abandoned active rooms with no live sockets."""
    while True:
        try:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            async with AsyncSessionLocal() as db:
                closed = await sweep_idle_rooms(db, idle_minutes=ROOM_IDLE_MINUTES)
                if closed:
                    logger.info("Idle room sweeper closed %s room(s)", closed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Idle room sweeper failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_room_sweeper_loop())
    logger.info(
        "Room idle sweeper started (every %ss, idle after %s min)",
        _SWEEP_INTERVAL_SECONDS,
        ROOM_IDLE_MINUTES,
    )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)
app.include_router(
    internal_router, prefix="/internal", tags=["internal"], include_in_schema=False
)
app.include_router(admin_router, prefix="/admin", tags=["admin"])


@app.get("/health")
async def health_check():
    return {"status": "ok"}

## server run command
# uvicorn app.main:app --reload 