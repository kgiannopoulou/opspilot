from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import User
from app.repositories.catalog import ServiceRepository, UserRepository
from app.schemas.catalog import ServiceOut
from app.schemas.common import UserRef

router = APIRouter(tags=["catalog"])


@router.get("/services", response_model=list[ServiceOut])
def list_services(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[ServiceOut]:
    return [ServiceOut.model_validate(s) for s in ServiceRepository(db).list()]


@router.get("/users", response_model=list[UserRef])
def list_users(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[UserRef]:
    """Active users — used by the UI's assignee picker."""
    return [UserRef.model_validate(u) for u in UserRepository(db).list()]
