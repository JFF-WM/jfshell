import asyncio

from src.plugins.experimental.notes._notes_manager import NotesManager


def run(coro):
    return asyncio.run(coro)


def make(tmp_path):
    return NotesManager(str(tmp_path / "notes.db"))


def test_add_and_get(tmp_path):
    async def go():
        manager = make(tmp_path)
        note_id = await manager.add_note("2026-11-23 10:00:00 hello", "body")
        notes = await manager.get_notes()
        assert [n.id for n in notes] == [note_id]
        assert notes[0].content == "2026-11-23 10:00:00 hello"
        assert notes[0].description == "body"
        assert notes[0].updated > 0

    run(go())


def test_edit_and_delete(tmp_path):
    async def go():
        manager = make(tmp_path)
        note_id = await manager.add_note("a")
        await manager.edit_note(note_id, "b", "desc")
        (note,) = await manager.get_notes()
        assert (note.content, note.description) == ("b", "desc")
        await manager.delete_note(note_id)
        assert await manager.get_notes() == []

    run(go())


def test_reminders_due_and_clear(tmp_path):
    async def go():
        manager = make(tmp_path)
        due = await manager.add_note("due")
        later = await manager.add_note("later")
        await manager.set_reminder(due, 100, 60, 2)
        await manager.set_reminder(later, 10_000, 60, 2)
        assert [n.id for n in await manager.get_due_reminders(500)] == [due]
        await manager.set_reminder(due, 0, 60, 0)
        assert await manager.get_due_reminders(500) == []

    run(go())


def test_restore_keeps_dates_and_reminder(tmp_path):
    async def go():
        manager = make(tmp_path)
        note_id = await manager.add_note("keep", "me")
        await manager.set_reminder(note_id, 4242, 300, 3)
        (note,) = await manager.get_notes()
        await manager.delete_note(note_id)
        new_id = await manager.restore_note(note)
        (restored,) = await manager.get_notes()
        assert restored.id == new_id
        assert restored[1:] == note[1:]

    run(go())


def test_clear_notes(tmp_path):
    async def go():
        manager = make(tmp_path)
        await manager.add_note("a")
        await manager.add_note("b")
        await manager.clear_notes()
        assert await manager.get_notes() == []

    run(go())


def test_upgrades_old_schema(tmp_path):
    import sqlite3

    path = tmp_path / "notes.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "content TEXT NOT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    con.execute("INSERT INTO notes (content) VALUES ('old note')")
    con.commit()
    con.close()

    async def go():
        manager = NotesManager(str(path))
        (note,) = await manager.get_notes()
        assert note.content == "old note"
        assert (note.description, note.remind_at, note.remind_remaining) == ("", 0, 0)

    run(go())
