from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRef(ORMModel):
    id: int
    email: str
    full_name: str


class ServiceRef(ORMModel):
    id: int
    name: str
    display_name: str


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
