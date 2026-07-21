# """Helpers for the school timetable feature: building API responses and
# importing/exporting timetables as Excel (.xlsx) files.
# """
# from __future__ import annotations

# import io
# from datetime import datetime, time

# from openpyxl import Workbook, load_workbook
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.models.class_ import Class
# from app.models.subject import Subject
# from app.models.teacher import Teacher
# from app.models.timetable_entry import TimetableEntry
# from app.schemas.timetable import (
#     DAY_NAMES,
#     DEFAULT_ACADEMIC_YEAR,
#     ClassBrief,
#     SubjectBrief,
#     TeacherBrief,
#     TimetableEntryOut,
#     TimetableImportResult,
# )

# DAY_ALIASES = {
#     "mon": "Monday", "monday": "Monday",
#     "tue": "Tuesday", "tues": "Tuesday", "tuesday": "Tuesday",
#     "wed": "Wednesday", "weds": "Wednesday", "wednesday": "Wednesday",
#     "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "thursday": "Thursday",
#     "fri": "Friday", "friday": "Friday",
#     "sat": "Saturday", "saturday": "Saturday",
# }

# TEMPLATE_HEADERS = [
#     "Class",
#     "Section",
#     "Day",
#     "Period",
#     "Start Time",
#     "End Time",
#     "Subject",
#     "Teacher Email",
#     "Room",
#     "Academic Year",
# ]


# def day_sort_index(day: str) -> int:
#     """Calendar order for a day name, since the column is stored as text and
#     would otherwise sort alphabetically (Friday before Monday, etc.)."""
#     try:
#         return DAY_NAMES.index(day)
#     except ValueError:
#         return len(DAY_NAMES)


# def _time_to_str(value: time) -> str:
#     return value.strftime("%H:%M")


# def _str_to_time(value: str) -> time:
#     parts = value.strip().split(":")
#     return time(hour=int(parts[0]), minute=int(parts[1]))


# def build_timetable_entry_out(entry: TimetableEntry) -> TimetableEntryOut:
#     return TimetableEntryOut(
#         id=entry.id,
#         school_id=entry.school_id,
#         class_id=entry.class_id,
#         subject_id=entry.subject_id,
#         teacher_id=entry.teacher_id,
#         day_of_week=entry.day_of_week,
#         period_number=entry.period_number,
#         start_time=_time_to_str(entry.start_time),
#         end_time=_time_to_str(entry.end_time),
#         room=entry.room,
#         academic_year=entry.academic_year,
#         class_=ClassBrief.model_validate(entry.class_) if entry.class_ else None,
#         subject=SubjectBrief.model_validate(entry.subject) if entry.subject else None,
#         teacher=TeacherBrief.model_validate(entry.teacher) if entry.teacher else None,
#         created_at=entry.created_at,
#         updated_at=entry.updated_at,
#     )


# def _parse_day(value) -> str | None:
#     if value is None or value == "":
#         return None
#     text = str(value).strip().lower()
#     return DAY_ALIASES.get(text)


# def _parse_time(value) -> str | None:
#     if value is None or value == "":
#         return None
#     if isinstance(value, time):
#         return value.strftime("%H:%M")
#     if isinstance(value, datetime):
#         return value.strftime("%H:%M")
#     text = str(value).strip().replace(".", ":")
#     parts = text.split(":")
#     if len(parts) < 2:
#         return None
#     try:
#         hour, minute = int(parts[0]), int(parts[1])
#     except ValueError:
#         return None
#     if not (0 <= hour <= 23 and 0 <= minute <= 59):
#         return None
#     return f"{hour:02d}:{minute:02d}"


# def build_template_workbook() -> io.BytesIO:
#     """Builds a blank importable timetable template with one example row."""
#     wb = Workbook()
#     ws = wb.active
#     ws.title = "Timetable"
#     ws.append(TEMPLATE_HEADERS)
#     ws.append(
#         [
#             "Class 10", "A", "Monday", 1, "08:00", "08:45",
#             "Mathematics", "teacher@example.com", "203", DEFAULT_ACADEMIC_YEAR,
#         ]
#     )
#     for column_cells in ws.columns:
#         length = max(len(str(cell.value)) for cell in column_cells)
#         ws.column_dimensions[column_cells[0].column_letter].width = max(12, length + 4)

#     buffer = io.BytesIO()
#     wb.save(buffer)
#     buffer.seek(0)
#     return buffer


# async def import_timetable_from_excel(
#     db: AsyncSession,
#     school_id: int,
#     file_bytes: bytes,
#     replace_existing: bool,
# ) -> TimetableImportResult:
#     try:
#         wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
#     except Exception as exc:  # noqa: BLE001 - surfaced to the user as a message
#         return TimetableImportResult(
#             created=0, updated=0, skipped=0,
#             errors=[f"Could not read the Excel file: {exc}"],
#         )

#     ws = wb.active
#     rows = list(ws.iter_rows(values_only=True))
#     if not rows:
#         return TimetableImportResult(created=0, updated=0, skipped=0, errors=["The sheet is empty."])

#     header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]

#     def col(*names: str) -> int | None:
#         for name in names:
#             if name in header:
#                 return header.index(name)
#         return None

#     idx_class = col("class")
#     idx_section = col("section")
#     idx_day = col("day", "day of week")
#     idx_period = col("period", "period number")
#     idx_start = col("start time", "start")
#     idx_end = col("end time", "end")
#     idx_subject = col("subject")
#     idx_teacher_email = col("teacher email", "teacher")
#     idx_room = col("room")
#     idx_year = col("academic year", "year")

#     required_missing = [
#         idx_class is None, idx_day is None, idx_period is None,
#         idx_start is None, idx_end is None, idx_subject is None,
#         idx_teacher_email is None,
#     ]
#     if any(required_missing):
#         return TimetableImportResult(
#             created=0, updated=0, skipped=0,
#             errors=[
#                 "The sheet is missing required columns. Expected at least: "
#                 "Class, Day, Period, Start Time, End Time, Subject, Teacher Email."
#             ],
#         )

#     class_rows = (
#         await db.execute(select(Class).where(Class.school_id == school_id))
#     ).scalars().all()
#     classes_by_key = {
#         (c.name.strip().lower(), (c.section or "").strip().lower()): c for c in class_rows
#     }

#     subject_rows = (
#         await db.execute(
#             select(Subject).join(Class, Subject.class_id == Class.id).where(Class.school_id == school_id)
#         )
#     ).scalars().all()
#     subjects_by_class: dict[int, dict[str, Subject]] = {}
#     for s in subject_rows:
#         subjects_by_class.setdefault(s.class_id, {})[s.name.strip().lower()] = s

#     teacher_rows = (
#         await db.execute(select(Teacher).where(Teacher.school_id == school_id))
#     ).scalars().all()
#     teachers_by_email = {t.email.strip().lower(): t for t in teacher_rows}

#     existing_entries = (
#         await db.execute(
#             select(TimetableEntry).where(TimetableEntry.school_id == school_id)
#         )
#     ).scalars().all()

#     class_slots: dict[tuple, TimetableEntry] = {
#         (e.class_id, e.day_of_week, e.period_number, e.academic_year): e for e in existing_entries
#     }
#     teacher_slots: dict[tuple, int] = {
#         (e.teacher_id, e.day_of_week, e.period_number, e.academic_year): e.class_id
#         for e in existing_entries
#     }

#     created = 0
#     updated = 0
#     skipped = 0
#     errors: list[str] = []

#     for row_number, row in enumerate(rows[1:], start=2):
#         if row is None or all(v is None or str(v).strip() == "" for v in row):
#             continue

#         class_name = row[idx_class] if idx_class < len(row) else None
#         section = (row[idx_section] if idx_section is not None and idx_section < len(row) else None) or "A"
#         if not class_name:
#             skipped += 1
#             errors.append(f"Row {row_number}: missing class name.")
#             continue

#         class_key = (str(class_name).strip().lower(), str(section).strip().lower())
#         class_ = classes_by_key.get(class_key)
#         if not class_:
#             skipped += 1
#             errors.append(f"Row {row_number}: class \"{class_name}\" section \"{section}\" was not found.")
#             continue

#         day = _parse_day(row[idx_day] if idx_day < len(row) else None)
#         if day is None:
#             skipped += 1
#             errors.append(f"Row {row_number}: could not read the day (\"{row[idx_day] if idx_day < len(row) else ''}\").")
#             continue

#         period_raw = row[idx_period] if idx_period < len(row) else None
#         try:
#             period = int(period_raw)
#         except (TypeError, ValueError):
#             skipped += 1
#             errors.append(f"Row {row_number}: could not read the period number.")
#             continue

#         start_time = _parse_time(row[idx_start] if idx_start < len(row) else None)
#         end_time = _parse_time(row[idx_end] if idx_end < len(row) else None)
#         if not start_time or not end_time:
#             skipped += 1
#             errors.append(f"Row {row_number}: start/end time must look like \"08:00\".")
#             continue

#         subject_name = str(row[idx_subject]).strip() if idx_subject < len(row) and row[idx_subject] else None
#         subject = subjects_by_class.get(class_.id, {}).get(subject_name.lower()) if subject_name else None
#         if not subject:
#             skipped += 1
#             errors.append(
#                 f"Row {row_number}: subject \"{subject_name or ''}\" was not found for {class_name}."
#             )
#             continue

#         teacher_email = str(row[idx_teacher_email]).strip() if idx_teacher_email < len(row) and row[idx_teacher_email] else None
#         teacher = teachers_by_email.get(teacher_email.lower()) if teacher_email else None
#         if not teacher:
#             skipped += 1
#             errors.append(f"Row {row_number}: teacher \"{teacher_email or ''}\" was not found.")
#             continue

#         room = str(row[idx_room]).strip() if idx_room is not None and idx_room < len(row) and row[idx_room] else None
#         academic_year = (
#             str(row[idx_year]).strip()
#             if idx_year is not None and idx_year < len(row) and row[idx_year]
#             else DEFAULT_ACADEMIC_YEAR
#         )

#         class_key_slot = (class_.id, day, period, academic_year)
#         teacher_key_slot = (teacher.id, day, period, academic_year)

#         existing = class_slots.get(class_key_slot)
#         teacher_busy_class = teacher_slots.get(teacher_key_slot)

#         if teacher_busy_class is not None and teacher_busy_class != class_.id:
#             skipped += 1
#             errors.append(
#                 f"Row {row_number}: {teacher.name} is already scheduled elsewhere on "
#                 f"{day}, period {period}."
#             )
#             continue

#         if existing and not replace_existing:
#             skipped += 1
#             errors.append(
#                 f"Row {row_number}: a period already exists for {class_name} on "
#                 f"{day}, period {period} (enable \"replace existing\" to overwrite)."
#             )
#             continue

#         start_t = _str_to_time(start_time)
#         end_t = _str_to_time(end_time)

#         if existing:
#             existing.start_time = start_t
#             existing.end_time = end_t
#             existing.subject_id = subject.id
#             existing.teacher_id = teacher.id
#             existing.room = room
#             teacher_slots[teacher_key_slot] = class_.id
#             updated += 1
#         else:
#             entry = TimetableEntry(
#                 school_id=school_id,
#                 class_id=class_.id,
#                 subject_id=subject.id,
#                 teacher_id=teacher.id,
#                 day_of_week=day,
#                 period_number=period,
#                 start_time=start_t,
#                 end_time=end_t,
#                 room=room,
#                 academic_year=academic_year,
#             )
#             db.add(entry)
#             class_slots[class_key_slot] = entry
#             teacher_slots[teacher_key_slot] = class_.id
#             created += 1

#     await db.commit()

#     return TimetableImportResult(created=created, updated=updated, skipped=skipped, errors=errors)




"""Helpers for the school timetable feature: building API responses and
importing/exporting timetables as Excel (.xlsx) files.
"""
from __future__ import annotations

import io
from datetime import datetime, time

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.class_ import Class
from app.models.subject import Subject
from app.models.teacher import Teacher
from app.models.timetable_entry import TimetableEntry
from app.schemas.timetable import (
    DAY_NAMES,
    DEFAULT_ACADEMIC_YEAR,
    ClassBrief,
    SubjectBrief,
    TeacherBrief,
    TimetableEntryOut,
    TimetableImportResult,
)

DAY_ALIASES = {
    "mon": "Monday", "monday": "Monday",
    "tue": "Tuesday", "tues": "Tuesday", "tuesday": "Tuesday",
    "wed": "Wednesday", "weds": "Wednesday", "wednesday": "Wednesday",
    "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "thursday": "Thursday",
    "fri": "Friday", "friday": "Friday",
    "sat": "Saturday", "saturday": "Saturday",
}

TEMPLATE_HEADERS = [
    "Class",
    "Section",
    "Day",
    "Period",
    "Start Time",
    "End Time",
    "Subject",
    "Teacher Email",
    "Room",
    "Academic Year",
]


def day_sort_index(day: str) -> int:
    """Calendar order for a day name, since the column is stored as text and
    would otherwise sort alphabetically (Friday before Monday, etc.)."""
    try:
        return DAY_NAMES.index(day)
    except ValueError:
        return len(DAY_NAMES)


def _time_to_str(value: time) -> str:
    return value.strftime("%H:%M")


def _str_to_time(value: str) -> time:
    parts = value.strip().split(":")
    return time(hour=int(parts[0]), minute=int(parts[1]))


def build_timetable_entry_out(entry: TimetableEntry) -> TimetableEntryOut:
    return TimetableEntryOut(
        id=entry.id,
        school_id=entry.school_id,
        class_id=entry.class_id,
        subject_id=entry.subject_id,
        teacher_id=entry.teacher_id,
        day_of_week=entry.day_of_week,
        period_number=entry.period_number,
        start_time=_time_to_str(entry.start_time),
        end_time=_time_to_str(entry.end_time),
        room=entry.room,
        academic_year=entry.academic_year,
        class_=ClassBrief.model_validate(entry.class_) if entry.class_ else None,
        subject=SubjectBrief.model_validate(entry.subject) if entry.subject else None,
        teacher=TeacherBrief.model_validate(entry.teacher) if entry.teacher else None,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _parse_day(value) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    return DAY_ALIASES.get(text)


def _parse_time(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    text = str(value).strip().replace(".", ":")
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def build_template_workbook() -> io.BytesIO:
    """Builds a blank importable timetable template with one example row."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Timetable"
    ws.append(TEMPLATE_HEADERS)
    ws.append(
        [
            "Class 10", "A", "Monday", 1, "08:00", "08:45",
            "Mathematics", "teacher@example.com", "203", DEFAULT_ACADEMIC_YEAR,
        ]
    )
    for column_cells in ws.columns:
        length = max(len(str(cell.value)) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = max(12, length + 4)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


async def import_timetable_from_excel(
    db: AsyncSession,
    school_id: int,
    file_bytes: bytes,
    replace_existing: bool,
) -> TimetableImportResult:
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a message
        return TimetableImportResult(
            created=0, updated=0, skipped=0,
            errors=[f"Could not read the Excel file: {exc}"],
        )

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return TimetableImportResult(created=0, updated=0, skipped=0, errors=["The sheet is empty."])

    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]

    def col(*names: str) -> int | None:
        for name in names:
            if name in header:
                return header.index(name)
        return None

    idx_class = col("class")
    idx_section = col("section")
    idx_day = col("day", "day of week")
    idx_period = col("period", "period number")
    idx_start = col("start time", "start")
    idx_end = col("end time", "end")
    idx_subject = col("subject")
    idx_teacher_email = col("teacher email", "teacher")
    idx_room = col("room")
    idx_year = col("academic year", "year")

    required_missing = [
        idx_class is None, idx_day is None, idx_period is None,
        idx_start is None, idx_end is None, idx_subject is None,
        idx_teacher_email is None,
    ]
    if any(required_missing):
        return TimetableImportResult(
            created=0, updated=0, skipped=0,
            errors=[
                "The sheet is missing required columns. Expected at least: "
                "Class, Day, Period, Start Time, End Time, Subject, Teacher Email."
            ],
        )

    class_rows = (
        await db.execute(select(Class).where(Class.school_id == school_id))
    ).scalars().all()
    classes_by_key = {
        (c.name.strip().lower(), (c.section or "").strip().lower()): c for c in class_rows
    }

    subject_rows = (
        await db.execute(
            select(Subject).join(Class, Subject.class_id == Class.id).where(Class.school_id == school_id)
        )
    ).scalars().all()
    subjects_by_class: dict[int, dict[str, Subject]] = {}
    for s in subject_rows:
        subjects_by_class.setdefault(s.class_id, {})[s.name.strip().lower()] = s

    teacher_rows = (
        await db.execute(select(Teacher).where(Teacher.school_id == school_id))
    ).scalars().all()
    teachers_by_email = {t.email.strip().lower(): t for t in teacher_rows}

    existing_entries = (
        await db.execute(
            select(TimetableEntry).where(TimetableEntry.school_id == school_id)
        )
    ).scalars().all()

    class_slots: dict[tuple, TimetableEntry] = {
        (e.class_id, e.day_of_week, e.period_number, e.academic_year): e for e in existing_entries
    }
    teacher_slots: dict[tuple, int] = {
        (e.teacher_id, e.day_of_week, e.period_number, e.academic_year): e.class_id
        for e in existing_entries
    }

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for row_number, row in enumerate(rows[1:], start=2):
        if row is None or all(v is None or str(v).strip() == "" for v in row):
            continue

        class_name = row[idx_class] if idx_class < len(row) else None
        section = (row[idx_section] if idx_section is not None and idx_section < len(row) else None) or "A"
        if not class_name:
            skipped += 1
            errors.append(f"Row {row_number}: missing class name.")
            continue

        class_key = (str(class_name).strip().lower(), str(section).strip().lower())
        class_ = classes_by_key.get(class_key)
        if not class_:
            skipped += 1
            errors.append(f"Row {row_number}: class \"{class_name}\" section \"{section}\" was not found.")
            continue

        day = _parse_day(row[idx_day] if idx_day < len(row) else None)
        if day is None:
            skipped += 1
            errors.append(f"Row {row_number}: could not read the day (\"{row[idx_day] if idx_day < len(row) else ''}\").")
            continue

        period_raw = row[idx_period] if idx_period < len(row) else None
        try:
            period = int(period_raw)
        except (TypeError, ValueError):
            skipped += 1
            errors.append(f"Row {row_number}: could not read the period number.")
            continue

        start_time = _parse_time(row[idx_start] if idx_start < len(row) else None)
        end_time = _parse_time(row[idx_end] if idx_end < len(row) else None)
        if not start_time or not end_time:
            skipped += 1
            errors.append(f"Row {row_number}: start/end time must look like \"08:00\".")
            continue

        subject_name = str(row[idx_subject]).strip() if idx_subject < len(row) and row[idx_subject] else None
        subject = subjects_by_class.get(class_.id, {}).get(subject_name.lower()) if subject_name else None
        if not subject:
            skipped += 1
            available = sorted(subjects_by_class.get(class_.id, {}).keys())
            available_text = ", ".join(available) if available else "none set up yet"
            errors.append(
                f"Row {row_number}: subject \"{subject_name or ''}\" was not found for "
                f"{class_name} (Section {section}). Subjects on that class: {available_text}."
            )
            continue

        teacher_email = str(row[idx_teacher_email]).strip() if idx_teacher_email < len(row) and row[idx_teacher_email] else None
        teacher = teachers_by_email.get(teacher_email.lower()) if teacher_email else None
        if not teacher:
            skipped += 1
            errors.append(
                f"Row {row_number}: teacher \"{teacher_email or ''}\" was not found. "
                f"Make sure this matches the teacher's login email exactly."
            )
            continue

        room = str(row[idx_room]).strip() if idx_room is not None and idx_room < len(row) and row[idx_room] else None
        academic_year = (
            str(row[idx_year]).strip()
            if idx_year is not None and idx_year < len(row) and row[idx_year]
            else DEFAULT_ACADEMIC_YEAR
        )

        class_key_slot = (class_.id, day, period, academic_year)
        teacher_key_slot = (teacher.id, day, period, academic_year)

        existing = class_slots.get(class_key_slot)
        teacher_busy_class = teacher_slots.get(teacher_key_slot)

        if teacher_busy_class is not None and teacher_busy_class != class_.id:
            skipped += 1
            errors.append(
                f"Row {row_number}: {teacher.name} is already scheduled elsewhere on "
                f"{day}, period {period}."
            )
            continue

        if existing and not replace_existing:
            skipped += 1
            errors.append(
                f"Row {row_number}: a period already exists for {class_name} on "
                f"{day}, period {period} (enable \"replace existing\" to overwrite)."
            )
            continue

        start_t = _str_to_time(start_time)
        end_t = _str_to_time(end_time)

        if existing:
            existing.start_time = start_t
            existing.end_time = end_t
            existing.subject_id = subject.id
            existing.teacher_id = teacher.id
            existing.room = room
            teacher_slots[teacher_key_slot] = class_.id
            updated += 1
        else:
            entry = TimetableEntry(
                school_id=school_id,
                class_id=class_.id,
                subject_id=subject.id,
                teacher_id=teacher.id,
                day_of_week=day,
                period_number=period,
                start_time=start_t,
                end_time=end_t,
                room=room,
                academic_year=academic_year,
            )
            db.add(entry)
            class_slots[class_key_slot] = entry
            teacher_slots[teacher_key_slot] = class_.id
            created += 1

    await db.commit()

    return TimetableImportResult(created=created, updated=updated, skipped=skipped, errors=errors)