from fastapi import APIRouter

from app.api import auth, catalog, dashboard, incidents, tickets, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tickets.router)
api_router.include_router(catalog.router)
api_router.include_router(users.router)
api_router.include_router(dashboard.router)
api_router.include_router(incidents.router)
