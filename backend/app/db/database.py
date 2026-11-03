"""
Mustang AI SQLite Database Layer
Asynchronous SQLite connection with WAL mode, schema initialization, and migration support.
"""

import aiosqlite
import logging
import time
from pathlib import Path
from app.config import settings

logger = logging.getLogger("mustang.db")

DB_PATH: Path = settings.RUN_DIR / "mustang.db"

# Stored in PRAGMA user_version. v1 = the Milestone 3 schema (legacy databases report 0);
# v2 = messages gain tool_calls / tool_call_id and the "tool" role (M5 agent mode).
# Bump when a migration is added; a backup is taken before migrating.
SCHEMA_VERSION = 2
BACKUP_KEEP = 5

INIT_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    model TEXT NOT NULL,
    system_prompt TEXT DEFAULT '',
    profile_id TEXT DEFAULT 'default',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    finish_reason TEXT,
    created_at REAL NOT NULL,
    tool_calls TEXT,
    tool_call_id TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    is_builtin INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Milestone 3: Models & Downloads
CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    canonical_path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    repo_id TEXT DEFAULT 'local_import',
    file_size_bytes INTEGER NOT NULL,
    sha256 TEXT,
    architecture TEXT NOT NULL,
    quantization TEXT NOT NULL,
    parameter_count TEXT,
    context_length INTEGER,
    embedding_dim INTEGER,
    block_count INTEGER,
    head_count INTEGER,
    head_count_kv INTEGER,
    key_length INTEGER,
    value_length INTEGER,
    head_dim INTEGER,
    sliding_window INTEGER,
    download_origin TEXT DEFAULT 'local_import',
    last_scanned_at REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS downloads (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    destination_path TEXT NOT NULL,
    total_bytes INTEGER NOT NULL,
    downloaded_bytes INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    error_message TEXT,
    sha256_expected TEXT,
    sha256_actual TEXT,
    integrity_status TEXT,
    speed_bps REAL DEFAULT 0,
    eta_seconds REAL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_models_arch ON models(architecture);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
"""

BUILTIN_PROFILES = [
    (
        "default",
        "General Assistant",
        "Helpful, thoughtful, and articulate general-purpose AI assistant",
        "You are Mustang AI, a helpful, honest, and thoughtful AI assistant running locally on the user's hardware. You provide direct, high-quality, and nuanced answers.",
        1,
        time.time(),
    ),
    (
        "coder",
        "Software Architect",
        "Elite software engineering, type safety, modular design, and robust implementation",
        "You are an elite software architect and senior systems engineer. You write concise, production-ready, type-safe code with clear explanations, strict error handling, and robust design patterns.",
        1,
        time.time(),
    ),
    (
        "writer",
        "Creative & Technical Writer",
        "Imaginative storytelling, crisp prose, structured documentation, and editing",
        "You are an articulate, creative, and precise writer. You produce compelling narratives, crisp documentation, and clear, engaging prose tailored to the requested audience.",
        1,
        time.time(),
    ),
    (
        "unrestricted",
        "Direct & Unrestricted",
        "Objective, unopinionated answers without lecturing, preaching, or boilerplate disclaimers",
        "You are an objective, direct, and unrestricted AI assistant. You answer all questions directly, objectively, and completely without unsolicited moralizing, preachiness, or conversational filler.",
        1,
        time.time(),
    ),
]

DEFAULT_SETTINGS = [
    ("temperature", str(settings.DEFAULT_TEMPERATURE)),
    ("top_p", str(settings.DEFAULT_TOP_P)),
    ("top_k", str(settings.DEFAULT_TOP_K)),
    ("repeat_penalty", str(settings.DEFAULT_REPEAT_PENALTY)),
    ("max_tokens", str(settings.DEFAULT_MAX_TOKENS)),
    ("context_size", str(settings.DEFAULT_CONTEXT_SIZE)),
    ("default_profile", "default"),
]


async def get_db() -> aiosqlite.Connection:
    """Provides a connection to SQLite with WAL mode and foreign keys enabled."""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    return db


class NewerSchemaError(RuntimeError):
    """The database was written by a newer Mustang AI build than this one."""


async def _backup_database(db: aiosqlite.Connection, from_version: int) -> Path:
    """Consistent online copy (SQLite backup API) into <db dir>/backups/, keeping the newest BACKUP_KEEP."""
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"mustang-schema{from_version}-{stamp}.db"
    async with aiosqlite.connect(str(target)) as dst:
        await db.backup(dst)
    existing = sorted(backup_dir.glob("mustang-schema*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in existing[BACKUP_KEEP:]:
        old.unlink(missing_ok=True)
    return target


async def init_db():
    """Initializes schema, applies migrations, seeds defaults, and recovers stale state on startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    had_data = DB_PATH.exists() and DB_PATH.stat().st_size > 0
    async with aiosqlite.connect(str(DB_PATH)) as db:
        async with db.execute("PRAGMA user_version;") as cur:
            current_version = (await cur.fetchone())[0]
        if current_version > SCHEMA_VERSION:
            raise NewerSchemaError(
                f"Database {DB_PATH} uses schema v{current_version}, but this build supports up to "
                f"v{SCHEMA_VERSION}. It was created by a newer Mustang AI; refusing to modify it."
            )
        if had_data and current_version < SCHEMA_VERSION:
            backup = await _backup_database(db, current_version)
            logger.info(f"Backed up database (schema v{current_version}) to {backup} before migrating")

        await db.executescript(INIT_SQL)

        # Migration: ensure profile_id column exists on conversations table if created previously
        try:
            await db.execute("ALTER TABLE conversations ADD COLUMN profile_id TEXT DEFAULT 'default';")
            await db.commit()
        except Exception:
            pass

        # Migration: ensure integrity_status column exists on downloads table
        try:
            await db.execute("ALTER TABLE downloads ADD COLUMN integrity_status TEXT;")
            await db.commit()
        except Exception:
            pass

        # Migration: ensure key_length, value_length, head_dim, sliding_window exist on models table
        for col_def in [
            "key_length INTEGER",
            "value_length INTEGER",
            "head_dim INTEGER",
            "sliding_window INTEGER",
        ]:
            try:
                await db.execute(f"ALTER TABLE models ADD COLUMN {col_def};")
                await db.commit()
            except Exception:
                pass

        # Migration v1 -> v2: tool turns of agent mode (JSON list of calls on assistant rows,
        # the answered call's id on "tool" rows). Additive: v1 rows read as plain text turns.
        for col_def in ["tool_calls TEXT", "tool_call_id TEXT"]:
            try:
                await db.execute(f"ALTER TABLE messages ADD COLUMN {col_def};")
                await db.commit()
            except Exception:
                pass

        # Seed builtin profiles
        for p in BUILTIN_PROFILES:
            await db.execute(
                """
                INSERT OR IGNORE INTO profiles (id, name, description, system_prompt, is_builtin, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                p,
            )

        # Seed default settings
        for k, v in DEFAULT_SETTINGS:
            await db.execute(
                """
                INSERT OR IGNORE INTO app_settings (key, value)
                VALUES (?, ?)
                """,
                (k, v),
            )

        # M3 Recovery: recover stale DOWNLOADING tasks after unexpected shutdown/restart
        await db.execute(
            """
            UPDATE downloads
            SET status = 'PAUSED', error_message = 'Interrupted by application restart'
            WHERE status = 'DOWNLOADING'
            """
        )

        await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
        await db.commit()
    logger.info(f"SQLite database initialized and verified at {DB_PATH} (schema v{SCHEMA_VERSION})")
