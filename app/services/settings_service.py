"""Operational settings that can be changed without a redeploy.

Each key has an environment variable behind it as the default. A row in
system_settings overrides that default; deleting the row restores it. Nothing
here is a credential — see the SystemSetting docstring for why that line is
drawn hard.

Reads go through a synchronous session and a short-lived process cache, for the
same reason email_log_service does: the consumers are split between async
request handlers (invite links, the maintenance middleware) and the Celery
worker (outgoing mail), and a sync engine is the one thing both can call.
The cache is what makes that affordable — invite_link() would otherwise hit the
database on every recipient of a bulk invite.

CACHE_TTL_SECONDS is therefore also the propagation delay: each process holds
its own cache, so a value changed in the admin panel reaches the worker within
half a minute rather than instantly. That is the right trade for settings that
are read constantly and written a few times a year, but it does mean "I changed
it and nothing happened" is worth waiting 30 seconds on before investigating.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app.config import settings as env_settings
from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    description: str
    # "string" | "bool" | "int"
    kind: str
    category: str
    # Called rather than captured: env_settings is read at import time and the
    # default has to reflect the current value, not the value at first import.
    default: Callable[[], Any]


SPECS: dict[str, SettingSpec] = {
    "maintenance_mode": SettingSpec(
        key="maintenance_mode",
        label="Maintenance mode",
        description=(
            "Blocks every write from teachers, students and school admins, and "
            "shows them a banner. Reading still works, and the super admin panel "
            "is unaffected so you can turn it back off."
        ),
        kind="bool",
        category="Availability",
        default=lambda: False,
    ),
    "maintenance_message": SettingSpec(
        key="maintenance_message",
        label="Maintenance message",
        description="Shown to users while maintenance mode is on.",
        kind="string",
        category="Availability",
        default=lambda: "Class Echo is briefly down for maintenance. Please try again shortly.",
    ),
    "email_paused": SettingSpec(
        key="email_paused",
        label="Pause outgoing email",
        description=(
            "Stops all outgoing mail without touching credentials. Messages are "
            "recorded in the operations log as 'skipped' rather than being lost, "
            "so you can see what would have gone out. Use when invitations are "
            "going to the wrong people, or to stay under a provider's daily cap."
        ),
        kind="bool",
        category="Email",
        default=lambda: False,
    ),
    "email_from_name": SettingSpec(
        key="email_from_name",
        label="Sender display name",
        description=(
            "The name recipients see instead of the bare sending address. "
            "Applies to SMTP and Mailjet alike."
        ),
        kind="string",
        category="Email",
        default=lambda: env_settings.MAILJET_FROM_NAME,
    ),
    "frontend_url": SettingSpec(
        key="frontend_url",
        label="App URL for emailed links",
        description=(
            "Every invite and verification link is built against this. Wrong "
            "value means the mail arrives and the link does not open — which "
            "used to need a redeploy to correct."
        ),
        kind="string",
        category="Email",
        default=lambda: env_settings.FRONTEND_URL,
    ),
    "invite_ttl_days": SettingSpec(
        key="invite_ttl_days",
        label="Invite link validity (days)",
        description=(
            "How long an invitation stays usable. Shorter is safer; longer helps "
            "when a school takes a week to get its staff onboarded."
        ),
        kind="int",
        category="Onboarding",
        default=lambda: 7,
    ),
}


_lock = threading.Lock()
_session_local = None
# (loaded_at_monotonic, {key: raw_value})
_cache: tuple[float, dict[str, str]] | None = None


def _sessions():
    global _session_local
    if _session_local is None:
        url = env_settings.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        _session_local = sessionmaker(bind=create_engine(url, pool_pre_ping=True))
    return _session_local


def _coerce(spec: SettingSpec, raw: str) -> Any:
    if spec.kind == "bool":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if spec.kind == "int":
        return int(raw.strip())
    return raw


def _serialise(spec: SettingSpec, value: Any) -> str:
    if spec.kind == "bool":
        return "true" if value else "false"
    if spec.kind == "int":
        return str(int(value))
    return str(value)


def _overrides(force: bool = False) -> dict[str, str]:
    """Every stored override, cached. Falls back to {} if the DB is unreachable.

    Returning {} on failure means a database problem degrades to environment
    defaults rather than taking the app down — the settings table is a
    convenience layer and must never become a dependency.
    """
    global _cache

    now = time.monotonic()
    with _lock:
        if not force and _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
            return _cache[1]

    try:
        with _sessions()() as session:
            rows = session.execute(select(SystemSetting)).scalars().all()
            loaded = {row.key: row.value for row in rows}
    except Exception:
        logger.warning(
            "Could not read system_settings; falling back to environment defaults",
            exc_info=True,
        )
        loaded = {} if _cache is None else _cache[1]

    with _lock:
        _cache = (now, loaded)
    return loaded


def invalidate() -> None:
    """Drop this process's cache so the next read hits the database."""
    global _cache
    with _lock:
        _cache = None


def get(key: str) -> Any:
    """Effective value: the stored override if there is one, else the env default."""
    spec = SPECS.get(key)
    if spec is None:
        raise KeyError(f"Unknown setting {key!r}")

    raw = _overrides().get(key)
    if raw is None:
        return spec.default()
    try:
        return _coerce(spec, raw)
    except (TypeError, ValueError):
        # A malformed stored value must not break the caller. The admin screen
        # validates on write, so this only happens if the row was edited by hand.
        logger.warning(
            "Stored value %r for %s is not a valid %s; using the default",
            raw,
            key,
            spec.kind,
        )
        return spec.default()


def describe() -> list[dict]:
    """Every setting with its effective value and where that value came from."""
    overrides = _overrides(force=True)
    out = []
    for spec in SPECS.values():
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "kind": spec.kind,
                "category": spec.category,
                "value": get(spec.key),
                "default": spec.default(),
                "is_overridden": spec.key in overrides,
            }
        )
    return out


def set_value(key: str, value: Any, updated_by: str | None = None) -> Any:
    """Store an override. Raises ValueError if the value does not fit the kind."""
    spec = SPECS.get(key)
    if spec is None:
        raise KeyError(f"Unknown setting {key!r}")

    if spec.kind == "int":
        coerced = int(value)
    elif spec.kind == "bool":
        coerced = bool(value) if isinstance(value, bool) else _coerce(spec, str(value))
    else:
        coerced = str(value)
        if not coerced.strip():
            raise ValueError(f"{spec.label} cannot be empty")

    raw = _serialise(spec, coerced)
    with _sessions()() as session:
        row = session.get(SystemSetting, key)
        if row is None:
            session.add(
                SystemSetting(key=key, value=raw, updated_by=updated_by)
            )
        else:
            row.value = raw
            row.updated_by = updated_by
            row.updated_at = datetime.utcnow()
        session.commit()

    invalidate()
    return coerced


def reset(key: str) -> Any:
    """Remove the override so the environment default applies again."""
    if key not in SPECS:
        raise KeyError(f"Unknown setting {key!r}")
    with _sessions()() as session:
        session.execute(delete(SystemSetting).where(SystemSetting.key == key))
        session.commit()
    invalidate()
    return SPECS[key].default()
