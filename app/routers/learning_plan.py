from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.deps import get_teacher, get_school_admin
from app.schemas.learning_plan import LearningPlanCreate, LearningPlanUpdate, LearningPlanOut
from app.services import learning_plan_service, chapter_service
from app.services.micro_planner_vision_service import (
    parse_and_create_micro_planner_entries,
    parse_and_create_subject_micro_planner_entries,
    import_subject_learning_plans_excel,
)

router = APIRouter()


@router.get("/teacher/subjects/{subject_id}/chapters/{chapter_id}/learning-plans", response_model=list[LearningPlanOut])
async def list_teacher_chapter_learning_plans(
    subject_id: int,
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_teacher),
):
    await chapter_service.get_teacher_owned_subject(db, subject_id, teacher.id)
    return await learning_plan_service.list_chapter_learning_plans(db, chapter_id)


@router.post("/teacher/subjects/{subject_id}/chapters/{chapter_id}/learning-plans", response_model=LearningPlanOut, status_code=201)
async def create_teacher_chapter_learning_plan(
    subject_id: int,
    chapter_id: int,
    payload: LearningPlanCreate,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_teacher),
):
    await chapter_service.get_teacher_owned_subject(db, subject_id, teacher.id)
    return await learning_plan_service.create_learning_plan(db, chapter_id, payload)


@router.post("/teacher/subjects/{subject_id}/chapters/{chapter_id}/learning-plans/import-photo", response_model=list[LearningPlanOut], status_code=201)
async def import_teacher_chapter_learning_plans_photo(
    subject_id: int,
    chapter_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_teacher),
):
    await chapter_service.get_teacher_owned_subject(db, subject_id, teacher.id)
    content = await file.read()
    mime = file.content_type or "image/jpeg"
    return await parse_and_create_micro_planner_entries(db, chapter_id, content, mime)


@router.post("/teacher/subjects/{subject_id}/learning-plans/import", response_model=list[LearningPlanOut], status_code=201)
async def import_teacher_subject_learning_plans_excel(
    subject_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_teacher),
):
    await chapter_service.get_teacher_owned_subject(db, subject_id, teacher.id)
    content = await file.read()
    return await import_subject_learning_plans_excel(db, subject_id, content)


@router.post("/teacher/subjects/{subject_id}/learning-plans/import-photo", response_model=list[LearningPlanOut], status_code=201)
async def import_teacher_subject_learning_plans_photo(
    subject_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_teacher),
):
    await chapter_service.get_teacher_owned_subject(db, subject_id, teacher.id)
    content = await file.read()
    mime = file.content_type or "image/jpeg"
    return await parse_and_create_subject_micro_planner_entries(db, subject_id, content, mime)


@router.put("/teacher/learning-plans/{plan_id}", response_model=LearningPlanOut)
async def update_teacher_learning_plan(
    plan_id: int,
    payload: LearningPlanUpdate,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_teacher),
):
    return await learning_plan_service.update_learning_plan(db, plan_id, payload)


@router.delete("/teacher/learning-plans/{plan_id}", status_code=204)
async def delete_teacher_learning_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_teacher),
):
    await learning_plan_service.delete_learning_plan(db, plan_id)
    return None


@router.get("/school/subjects/{subject_id}/chapters/{chapter_id}/learning-plans", response_model=list[LearningPlanOut])
async def list_school_chapter_learning_plans(
    subject_id: int,
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await chapter_service.get_owned_subject(db, subject_id, school_admin.school_id)
    return await learning_plan_service.list_chapter_learning_plans(db, chapter_id)


@router.post("/school/subjects/{subject_id}/chapters/{chapter_id}/learning-plans", response_model=LearningPlanOut, status_code=201)
async def create_school_chapter_learning_plan(
    subject_id: int,
    chapter_id: int,
    payload: LearningPlanCreate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await chapter_service.get_owned_subject(db, subject_id, school_admin.school_id)
    return await learning_plan_service.create_learning_plan(db, chapter_id, payload)


@router.post("/school/subjects/{subject_id}/chapters/{chapter_id}/learning-plans/import-photo", response_model=list[LearningPlanOut], status_code=201)
async def import_school_chapter_learning_plans_photo(
    subject_id: int,
    chapter_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await chapter_service.get_owned_subject(db, subject_id, school_admin.school_id)
    content = await file.read()
    mime = file.content_type or "image/jpeg"
    return await parse_and_create_micro_planner_entries(db, chapter_id, content, mime)


@router.post("/school/subjects/{subject_id}/learning-plans/import", response_model=list[LearningPlanOut], status_code=201)
async def import_school_subject_learning_plans_excel(
    subject_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await chapter_service.get_owned_subject(db, subject_id, school_admin.school_id)
    content = await file.read()
    return await import_subject_learning_plans_excel(db, subject_id, content)


@router.post("/school/subjects/{subject_id}/learning-plans/import-photo", response_model=list[LearningPlanOut], status_code=201)
async def import_school_subject_learning_plans_photo(
    subject_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await chapter_service.get_owned_subject(db, subject_id, school_admin.school_id)
    content = await file.read()
    mime = file.content_type or "image/jpeg"
    return await parse_and_create_subject_micro_planner_entries(db, subject_id, content, mime)


@router.put("/school/learning-plans/{plan_id}", response_model=LearningPlanOut)
async def update_school_learning_plan(
    plan_id: int,
    payload: LearningPlanUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await learning_plan_service.update_learning_plan(db, plan_id, payload)


@router.delete("/school/learning-plans/{plan_id}", status_code=204)
async def delete_school_learning_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await learning_plan_service.delete_learning_plan(db, plan_id)
    return None
