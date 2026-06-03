from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import BASE_DIR
from app.database import initialize_database, initialize_storage
from app.routers.admin import router as admin_router
from app.routers.chat import router as chat_router
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

    @app.get("/")
    def home():
        return {"message": "Server is running"}

    @app.get("/app")
    def frontend_app():
        return FileResponse(BASE_DIR / "frontend" / "index.html")

    app.include_router(chat_router)
    app.include_router(admin_router)
    app.include_router(uploads_router)
    app.mount(
        "/frontend",
        StaticFiles(directory=BASE_DIR / "frontend"),
        name="frontend",
    )

    return app
