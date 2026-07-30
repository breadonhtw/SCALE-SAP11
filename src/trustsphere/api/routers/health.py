from fastapi import APIRouter, Depends

from trustsphere.api.deps import get_repo
from trustsphere.persistence.base import Repository

router = APIRouter(tags=["health"])


@router.get("/health")
def health(repo: Repository = Depends(get_repo)):
    return repo.health_check()
