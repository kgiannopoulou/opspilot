from fastapi import APIRouter

from app.api import ai, audit, auth, catalog, dashboard, incidents, jobs, knowledge, sql, tickets, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tickets.router)
api_router.include_router(catalog.router)
api_router.include_router(users.router)
api_router.include_router(dashboard.router)
api_router.include_router(incidents.router)
api_router.include_router(knowledge.router)
api_router.include_router(sql.router)
api_router.include_router(jobs.router)
api_router.include_router(audit.router)
api_router.include_router(ai.router)
