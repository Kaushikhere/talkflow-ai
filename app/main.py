from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import logging

from app.config import BASE_DIR, KB_ENABLED
from app.database import initialize_database, initialize_storage

logger = logging.getLogger(__name__)
from app.routers.admin import router as admin_router
from app.routers.chat import router as chat_router
from app.routers.kb import router as kb_router
from app.routers.uploads import router as uploads_router


def create_app() -> FastAPI:
    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    initialize_storage()
    initialize_database()

    @app.on_event("startup")
    def warmup_kb_on_startup() -> None:
        if not KB_ENABLED:
            return
        try:
            from app.config import KB_RERANK_ENABLED
            from app.services.kb_embeddings import warmup_kb_embeddings
            from app.services.kb_rerank import warmup_kb_reranker
            from app.services.kb_retrieval import refresh_kb_document_cache

            warmup_kb_embeddings()
            refresh_kb_document_cache()
            if KB_RERANK_ENABLED:
                warmup_kb_reranker()
        except Exception as exc:
            logger.error(
                "KB warmup failed; chat will work but KB retrieval may be unavailable: %s",
                exc,
            )

    @app.get("/")
    def home():
        return {"message": "Server is running"}

    @app.get("/app")
    def frontend_app():
        return FileResponse(BASE_DIR / "frontend" / "index.html")

    app.include_router(chat_router)
    app.include_router(kb_router)
    app.include_router(admin_router)
    app.include_router(uploads_router)
    app.mount(
        "/frontend",
        StaticFiles(directory=BASE_DIR / "frontend"),
        name="frontend",
    )

    return app
