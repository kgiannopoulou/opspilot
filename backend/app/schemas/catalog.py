from app.schemas.common import ORMModel


class ServiceOut(ORMModel):
    id: int
    name: str
    display_name: str
    tier: int
    owner_team: str
    description: str
