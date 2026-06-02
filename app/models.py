from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class MaintenanceRequest(BaseModel):
    enabled: bool
