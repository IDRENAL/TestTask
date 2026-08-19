"""Public API request and response schemas."""

from pydantic import BaseModel, Field, field_validator


class ScriptView(BaseModel):
    id: int
    name: str
    filename: str
    cron_expression: str
    enabled: bool
    running: bool


class ScheduleUpdate(BaseModel):
    cron_expression: str = Field(min_length=9, max_length=100)

    @field_validator("cron_expression")
    @classmethod
    def normalize_cron(cls, value: str) -> str:
        return " ".join(value.split())


class ActionAccepted(BaseModel):
    accepted: bool = True
