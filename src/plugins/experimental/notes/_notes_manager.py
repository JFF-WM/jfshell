"""Async SQLite storage for the notes plugin."""

import asyncio
from typing import NamedTuple

import aiosqlite


class Note(NamedTuple):
    id: int
    content: str
    description: str
    remind_at: int
    remind_interval: int
    remind_remaining: int
    updated: int  # unix seconds of the last change


_COLUMNS = (
    "id, content, description, remind_at, remind_interval, remind_remaining, "
    "CAST(strftime('%s', timestamp) AS INTEGER)"
)


class NotesManager:
    """All database access for notes; every call opens its own connection."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._ready = False
        self._init_lock = asyncio.Lock()

    async def initialize_db(self):
        """Create the schema, or add the columns older databases lack."""
        async with self._init_lock:
            if self._ready:
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        remind_at INTEGER DEFAULT 0,
                        remind_interval INTEGER DEFAULT 0,
                        remind_remaining INTEGER DEFAULT 0
                    )
                    """
                )
                cursor = await db.execute("PRAGMA table_info(notes)")
                columns = {row[1] for row in await cursor.fetchall()}
                for name, ddl in (
                    ("description", "TEXT DEFAULT ''"),
                    ("remind_at", "INTEGER DEFAULT 0"),
                    ("remind_interval", "INTEGER DEFAULT 0"),
                    ("remind_remaining", "INTEGER DEFAULT 0"),
                ):
                    if name not in columns:
                        await db.execute(f"ALTER TABLE notes ADD COLUMN {name} {ddl}")
                await db.commit()
            self._ready = True

    async def get_notes(self) -> list[Note]:
        """All notes, most recently changed first."""
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"SELECT {_COLUMNS} FROM notes ORDER BY timestamp DESC, id DESC"
            )
            return [Note(*row) for row in await cursor.fetchall()]

    async def get_due_reminders(self, now: int) -> list[Note]:
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"SELECT {_COLUMNS} FROM notes "
                "WHERE remind_remaining > 0 AND remind_at > 0 AND remind_at <= ?",
                (now,),
            )
            return [Note(*row) for row in await cursor.fetchall()]

    async def add_note(self, content: str, description: str = "") -> int:
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO notes (content, description) VALUES (?, ?)",
                (content, description),
            )
            await db.commit()
            return cursor.lastrowid

    async def restore_note(self, note: Note) -> int:
        """Re-insert a deleted note with its original dates and reminder."""
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO notes (content, description, timestamp, remind_at, "
                "remind_interval, remind_remaining) "
                "VALUES (?, ?, datetime(?, 'unixepoch'), ?, ?, ?)",
                (
                    note.content,
                    note.description,
                    note.updated,
                    note.remind_at,
                    note.remind_interval,
                    note.remind_remaining,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def edit_note(self, note_id: int, content: str, description: str = ""):
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE notes SET content = ?, description = ?, "
                "timestamp = CURRENT_TIMESTAMP WHERE id = ?",
                (content, description, note_id),
            )
            await db.commit()

    async def delete_note(self, note_id: int):
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            await db.commit()

    async def set_reminder(
        self, note_id: int, remind_at: int, interval: int, remaining: int
    ):
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE notes SET remind_at = ?, remind_interval = ?, "
                "remind_remaining = ? WHERE id = ?",
                (remind_at, interval, remaining, note_id),
            )
            await db.commit()

    async def clear_notes(self):
        await self.initialize_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM notes")
            await db.commit()
