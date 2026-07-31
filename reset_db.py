"""
reset_db.py — Wipe all application data and seed a fresh Super Admin.

This script:
  1. Truncates every table (in reverse FK dependency order) so no FK
     violations occur.
  2. Creates a new Super Admin using the credentials passed as CLI args
     (or falls back to ADMIN_* env vars / hard-coded defaults below).

Usage:
    python reset_db.py --email admin@classecho.com --password password123 --name "Super Admin"

    # Or rely on ADMIN_* env vars:
    python reset_db.py

WARNING: This PERMANENTLY deletes ALL data from the database.
         Use with extreme caution in production.
"""

import asyncio
import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import app.models  # noqa: F401 — registers all ORM models with SQLAlchemy
from app.core.security import hash_password
from app.database import AsyncSessionLocal, engine
from app.models.admin import Admin

from sqlalchemy import text

# ── Table truncation order (children before parents) ──────────────────────────
# Must respect FK constraints: delete child rows before parent rows.
TRUNCATE_ORDER = [
    "chapter_chunks",
    "chapter_knowledge",
    "chapter_documents",
    "llm_reports",
    "recordings",
    "chapters",
    "timetable_entries",
    "subjects",
    "students",
    "teachers",
    "classes",
    "school_admins",
    "schools",
    "admins",
]


async def truncate_all_tables():
    """Wipe the database schema completely to allow a fresh migration.
    
    This reflects all tables (including alembic_version) and drops them.
    """
    async with engine.begin() as conn:
        try:
            # The cleanest way to wipe a Postgres database is to drop and recreate the public schema.
            # This wipes all tables, types (like vector), and alembic tracking data.
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            await conn.execute(text("CREATE SCHEMA public;"))
            await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
            print("  [OK] Dropped and recreated 'public' schema.")
        except Exception as e:
            print(f"  [!] Failed to drop schema public (possibly due to permissions): {e}")
            print("  [wait] Attempting to drop tables individually via reflection...")
            
            # Fallback: drop all tables individually using a synchronous connection
            def drop_everything(connection):
                from sqlalchemy import inspect, MetaData
                inspector = inspect(connection)
                meta = MetaData()
                meta.reflect(bind=connection)
                meta.drop_all(bind=connection)
                
                # Also drop alembic_version if it wasn't in meta
                if inspector.has_table("alembic_version"):
                    connection.execute(text("DROP TABLE alembic_version;"))
                    
            await conn.run_sync(drop_everything)
            print("  [OK] Dropped all tables individually.")
    print()


async def main(name: str, email: str, password: str):
    print("=" * 60)
    print("  Class Echo — Database Reset")
    print("=" * 60)
    print()

    print("Step 1: Truncating all tables ...")
    await truncate_all_tables()

    print()
    print("=" * 60)
    print("  [OK]  Database reset complete!")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Wipe the Class Echo database and seed a fresh Super Admin."
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("ADMIN_EMAIL", "admin@classecho.com"),
        help="Super admin email (default: ADMIN_EMAIL env var)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("ADMIN_PASSWORD", "password123"),
        help="Super admin password (default: ADMIN_PASSWORD env var)",
    )
    parser.add_argument(
        "--name",
        default=os.environ.get("ADMIN_NAME", "Super Admin"),
        help="Super admin display name (default: ADMIN_NAME env var)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )

    args = parser.parse_args()

    if not args.yes:
        print()
        print("⚠️  WARNING: This will DELETE ALL DATA in the database!")
        print(f"   Email    : {args.email}")
        print(f"   Name     : {args.name}")
        confirm = input("   Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)
        print()

    asyncio.run(main(name=args.name, email=args.email, password=args.password))
