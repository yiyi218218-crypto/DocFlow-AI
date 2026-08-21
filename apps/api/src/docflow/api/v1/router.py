from __future__ import annotations

from fastapi import APIRouter

from docflow.api.v1.routes import auth, blobs, documents

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(blobs.router)

__all__ = ["api_router"]
