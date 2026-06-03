from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None
    file_ids: list[int] | None = None


class MaintenanceRequest(BaseModel):
    enabled: bool