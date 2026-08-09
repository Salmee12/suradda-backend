from fastapi import APIRouter

from app.api.v1.endpoints import auth
from app.api.v1.endpoints import songs


api_router = APIRouter()

api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["auth"],
)

api_router.include_router(
    songs.router,
    prefix="/songs",
    tags=["songs"],
)