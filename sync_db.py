import asyncio
from sqlalchemy import text
import app.models  # noqa: F401
from app.database import Base, engine


async def main():
    print("Connecting to database and applying schema updates...")
    async with engine.begin() as conn:
        # Create all tables (creates learning_plans table if not exists)
        # await conn.run_sync(Base.metadata.create_all)  # Disabled to prevent conflicts with Alembic

        # Add missing column learning_plan_id to recordings table if not present
        await conn.execute(
            text(
                "ALTER TABLE recordings ADD COLUMN IF NOT EXISTS learning_plan_id INTEGER REFERENCES learning_plans(id) ON DELETE SET NULL;"
            )
        )

    print("Database schema successfully synchronized!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
