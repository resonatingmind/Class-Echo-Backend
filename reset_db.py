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
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

import app.models  # noqa: F401 — registers all ORM models with SQLAlchemy
from app.config import settings
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


ALLOW_REMOTE_WIPE_ENV = "ALLOW_REMOTE_DB_WIPE"


def assert_wipe_allowed() -> None:
    """
    Refuse to run against a non-local database unless explicitly overridden.

    start.sh invokes this script with --yes whenever ENVIRONMENT=test or
    RESET_DB_ON_STARTUP=true, so a stray env var on a hosted deploy would drop
    the entire schema on every boot -- silently, since --yes skips the prompt.
    That is survivable against a throwaway container and unrecoverable against
    a managed database like Supabase, which is why the host is checked here
    rather than trusted to the caller.
    """
    if settings.db_is_local:
        return

    host = urlparse(settings.sync_migration_url).hostname or "<unknown>"

    if os.environ.get(ALLOW_REMOTE_WIPE_ENV, "").lower() in {"1", "true", "yes"}:
        print(f"  [!] {ALLOW_REMOTE_WIPE_ENV} is set — wiping REMOTE host {host}")
        return

    print()
    print("=" * 60)
    print("  REFUSING TO WIPE A REMOTE DATABASE")
    print("=" * 60)
    print(f"  Host: {host}")
    print()
    print("  This is not a local database, and reset_db.py drops the entire")
    print("  public schema. Nothing here is recoverable.")
    print()
    print("  If you genuinely intend to wipe this database, re-run with:")
    print(f"      {ALLOW_REMOTE_WIPE_ENV}=true python reset_db.py --yes")
    print()
    print("  If you did NOT expect this to run, check ENVIRONMENT and")
    print("  RESET_DB_ON_STARTUP in your deploy environment — start.sh calls")
    print("  this script automatically when either is set.")
    print("=" * 60)
    sys.exit(1)


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

    assert_wipe_allowed()

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
        print(f"   Host     : {urlparse(settings.sync_migration_url).hostname}")
        print(f"   Email    : {args.email}")
        print(f"   Name     : {args.name}")
        confirm = input("   Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)
        print()

    asyncio.run(main(name=args.name, email=args.email, password=args.password))
