from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None
    file_ids: list[int] | None = None
    stream: bool = True
    use_knowledge_base: bool = True


class KbIngestRequest(BaseModel):
    no_scrape: bool = Field(
        default=True,
        description="Only ingest PDFs already under data/kb/ (skip web scrape)",
    )
    force: bool = Field(
        default=False,
        description="Re-index PDFs even if already indexed",
    )
    brochure_only: bool = False
    brochure_html: str | None = None
    background: bool = Field(
        default=True,
        description="Run ingest in background (recommended for large libraries)",
    )


class MaintenanceRequest(BaseModel):
    enabled: bool