from fastapi import APIRouter


from fastapi import APIRouter
from app.api.v1.endpoints import auth, songs, room, room_ws

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(songs.router, prefix="/songs", tags=["songs"])
api_router.include_router(room.router, prefix="/rooms", tags=["rooms"])
api_router.include_router(room_ws.router, prefix="/ws", tags=["websocket"])