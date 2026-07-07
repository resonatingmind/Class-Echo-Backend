import asyncio
from sqlalchemy import select
from dotenv import load_dotenv

load_dotenv()

import app.models
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.class_ import Class
from app.models.teacher import Teacher
from app.models.subject import Subject
from app.models.student import Student

async def main():
    async with AsyncSessionLocal() as session:
        # Check if Sarah Connor already exists as a teacher
        existing_teacher = await session.execute(select(Teacher).where(Teacher.email == "sarah@greenwood.com"))
        if existing_teacher.scalar_one_or_none():
            print("Demo data already seeded!")
            return

        # 1. Create School
        school = School(
            name="Greenwood High School",
            address="123 Educational Way, Springfield"
        )
        session.add(school)
        await session.commit()
        await session.refresh(school)
        print(f"Created School: {school.name} (ID: {school.id})")

        # 2. Create School Admin
        school_admin = SchoolAdmin(
            name="Principal Skinner",
            email="admin@greenwood.com",
            hashed_password=hash_password("password123"),
            school_id=school.id
        )
        session.add(school_admin)
        print("Created School Admin: admin@greenwood.com")

        # 3. Create Class
        class_obj = Class(
            name="Grade 10-A",
            school_id=school.id
        )
        session.add(class_obj)
        await session.commit()
        await session.refresh(class_obj)
        print(f"Created Class: {class_obj.name} (ID: {class_obj.id})")

        # 4. Create Teacher
        teacher = Teacher(
            name="Sarah Connor",
            email="sarah@greenwood.com",
            hashed_password=hash_password("password123"),
            school_id=school.id
        )
        session.add(teacher)
        await session.commit()
        await session.refresh(teacher)
        print(f"Created Teacher: {teacher.name} (ID: {teacher.id})")

        # 5. Create Subject
        subject = Subject(
            name="Mathematics",
            class_id=class_obj.id,
            teacher_id=teacher.id
        )
        session.add(subject)
        print("Created Subject: Mathematics")

        # 6. Create Student
        student = Student(
            name="John Connor",
            email="john@greenwood.com",
            hashed_password=hash_password("password123"),
            school_id=school.id,
            class_id=class_obj.id,
            mobile_number="1234567890"
        )
        session.add(student)
        await session.commit()
        print("Created Student: john@greenwood.com")
        print("All demo data seeded successfully!")

if __name__ == "__main__":
    asyncio.run(main())
