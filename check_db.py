import asyncio
from app.database import engine
from sqlalchemy import text

async def main():
    async with engine.begin() as conn:
        result = await conn.execute(text('SELECT * FROM alembic_version'))
        print(result.fetchall())

asyncio.run(main())
