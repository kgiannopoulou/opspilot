from fastapi import APIRouter

from app.api import catalog, tickets

api_router = APIRouter()
api_router.include_router(tickets.router)
api_router.include_router(catalog.router)
