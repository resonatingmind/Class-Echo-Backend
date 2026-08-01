from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.school import School
from app.models.class_ import Class
from app.schemas.school import SchoolOut
from app.schemas.class_ import ClassOut

router = APIRouter()


@router.get("/schools", response_model=list[SchoolOut])
async def public_list_schools(
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(School).options(selectinload(School.admin))
    if search:
        query = query.where(School.name.ilike(f"%{search}%"))
    result = await db.execute(query.order_by(School.name))
    return result.scalars().all()


@router.get("/schools/{school_id}/classes", response_model=list[ClassOut])
async def public_list_classes(
    school_id: int,
    db: AsyncSession = Depends(get_db),
):
    school = await db.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    result = await db.execute(
        select(Class).where(Class.school_id == school_id).order_by(
    Class.name,
    Class.section,
)
    )
    return result.scalars().all()


@router.get("/learning-plans/subject-template")
async def download_subject_learning_plan_template():
    """Download a blank Excel template for subject-level learning plans."""
    from app.services.learning_plan_service import build_subject_learning_plan_template_workbook
    buffer = build_subject_learning_plan_template_workbook()
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=subject_planner_template.xlsx"},
    )
