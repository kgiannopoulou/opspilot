from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class ServiceOut(ORMModel):
    id: int
    name: str
    display_name: str
    tier: int
    owner_team: str
    description: str
    dependencies: list[str] = []

    @field_validator("dependencies", mode="before")
    @classmethod
    def _names(cls, value: object) -> object:
        if isinstance(value, list):
            return [getattr(v, "name", v) for v in value]
        return value


class ServiceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(min_length=2, max_length=120)
    tier: int = Field(ge=1, le=4)
    owner_team: str = Field(min_length=2, max_length=64)
    description: str = ""
    dependencies: list[str] = []
