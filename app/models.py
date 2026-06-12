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


class PolicyRecommendRequest(BaseModel):
    age: int = Field(ge=1, le=120)
    budget_monthly: int | None = Field(default=None, ge=0)
    pre_existing: bool = False
    family_size: int = Field(default=1, ge=1, le=20)
    priorities: list[str] = Field(default_factory=list)


class MaintenanceRequest(BaseModel):
    enabled: bool


class AuditChatRequest(BaseModel):
    message: str
    stream: bool = True