from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.api import api_router
from app.api.internal import router as internal_router
from app.api.admin import router as admin_router

app = FastAPI(title=settings.PROJECT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this later
    # False, not True: auth travels in the Authorization header, never in a
    # cookie, so nothing needs credentialed cross-origin requests — and "*"
    # combined with credentials is a pairing browsers reject anyway.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)
# Deliberately outside the versioned prefix and out of the schema: these are
# called by the PHP tier only, gated by a shared secret, and have no business
# appearing in /docs alongside the public API.
app.include_router(
    internal_router, prefix="/internal", tags=["internal"], include_in_schema=False
)
app.include_router(admin_router, prefix="/admin", tags=["admin"])

@app.get("/health")
async def health_check():
    return {"status": "ok"}



## server run command
# uvicorn app.main:app --reload 