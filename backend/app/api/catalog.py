from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require
from app.auth.permissions import Permission
from app.core.database import get_db
from app.core.errors import ConflictError
from app.models import Service, User
from app.repositories.catalog import ServiceRepository
from app.schemas.catalog import ServiceCreate, ServiceOut
from app.services import audit

router = APIRouter(prefix="/services", tags=["services"])


@router.get("", response_model=list[ServiceOut])
def list_services(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[ServiceOut]:
    return [ServiceOut.model_validate(s) for s in ServiceRepository(db).list()]


@router.post("", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
def create_service(
    data: ServiceCreate, db: Session = Depends(get_db), admin: User = Depends(require(Permission.SERVICES_MANAGE))
) -> ServiceOut:
    repo = ServiceRepository(db)
    if repo.get_by_name(data.name):
        raise ConflictError(f"service {data.name} already exists")
    service = Service(**data.model_dump(exclude={"dependencies"}))
    service.dependencies = [d for name in data.dependencies if (d := repo.get_by_name(name))]
    db.add(service)
    db.flush()
    audit.record(db, admin, "service.created", "service", service.name, data.model_dump())
    db.commit()
    return ServiceOut.model_validate(service)
