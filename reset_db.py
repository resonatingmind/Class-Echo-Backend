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
    """Truncate every table using CASCADE to handle FK constraints.
    
    Uses a single TRUNCATE statement with all tables and CASCADE — this works
    on Render's managed Postgres where session_replication_role is restricted.
    """
    # Build a comma-separated list of quoted table names
    tables_sql = ", ".join(f'"{t}"' for t in TRUNCATE_ORDER)
    async with engine.begin() as conn:
        try:
            await conn.execute(
                text(f"TRUNCATE TABLE {tables_sql} RESTART IDENTITY CASCADE;")
            )
            for table in TRUNCATE_ORDER:
                print(f"  [OK] Truncated: {table}")
        except Exception as e:
            # Some tables might not exist yet — fall back
            # to truncating one by one and skip missing ones
            print(f"  [!]  Bulk truncate failed, trying one by one ...")
            for table in TRUNCATE_ORDER:
                try:
                    await conn.execute(
                        text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE;')
                    )
                    print(f"  [OK] Truncated: {table}")
                except Exception as inner_e:
                    print(f"  [--] Skipped '{table}': {inner_e}")
    print()


async def seed_admin(name: str, email: str, password: str):
    """Insert the new Super Admin."""
    async with AsyncSessionLocal() as session:
        admin = Admin(
            name=name,
            email=email.strip().lower(),
            hashed_password=hash_password(password),
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
    print(f"  [OK] Admin created: {admin.name} <{admin.email}> (id={admin.id})")
    print("    -> Log in via POST /auth/login")


async def main(name: str, email: str, password: str):
    print("=" * 60)
    print("  Class Echo — Database Reset")
    print("=" * 60)
    print()

    print("Step 1: Truncating all tables ...")
    await truncate_all_tables()

    print("Step 2: Seeding new Super Admin ...")
    await seed_admin(name, email, password)

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
