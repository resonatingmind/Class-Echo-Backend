from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_school_admin
from app.database import get_db

from app.schemas.planner import (
    PlannerItemOut,
    PlannerSubjectDetailOut,
)

from app.services.planner_service import (
    get_school_planner,
    get_planner_subject_detail,
)

router = APIRouter(
    prefix="/planner",
    tags=["Planner"],
)


@router.get(
    "",
    response_model=list[PlannerItemOut],
)
async def list_planner(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await get_school_planner(
        db=db,
        school_id=school_admin.school_id,
    )
    

@router.get(
    "/{subject_id}",
    response_model=PlannerSubjectDetailOut,
)
async def planner_subject_detail(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await get_planner_subject_detail(
        db=db,
        school_id=school_admin.school_id,
        subject_id=subject_id,
    )