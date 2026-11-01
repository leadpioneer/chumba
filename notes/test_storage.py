from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storage import Conflict, NotesError, NotesStore, StorageError  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chumba-notes-test-")
        self.store = NotesStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, title="Project", body="Body", key="k1"):
        return self.store.create(
            title=title,
            type="project",
            body=body,
            sources=["docs/report.md"],
            author="user",
            idempotency_key=key,
        )

    def test_create_read_and_markdown_file(self):
        note = self.create()
        self.assertTrue((Path(self.tmp.name) / "notes" / f"{note['id']}.md").exists())
        read = self.store.get(note["id"])
        self.assertEqual(read["title"], "Project")
        self.assertEqual(read["body"], "Body")
        self.assertEqual(read["revision"], note["revision"])
        self.assertEqual(read["sources"], ["docs/report.md"])

    def test_stale_edit_conflicts_and_returns_current_revision(self):
        note = self.create()
        updated = self.store.edit(
            note["id"],
            expected_revision=note["revision"],
            changes={"body": "New body"},
            author="user",
            idempotency_key="edit1",
        )
        self.assertNotEqual(updated["revision"], note["revision"])
        with self.assertRaises(Conflict) as context:
            self.store.edit(
                note["id"],
                expected_revision=note["revision"],
                changes={"body": "Stale"},
                author="agent",
                idempotency_key="edit2",
            )
        self.assertEqual(context.exception.details["current_revision"], updated["revision"])

    def test_idempotent_create_and_conflict_on_different_payload(self):
        first = self.create(key="same")
        second = self.create(key="same")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["revision"], second["revision"])
        with self.assertRaises(Conflict):
            self.create(title="Different", key="same")

    def test_idempotent_edit_retry(self):
        note = self.create()
        first = self.store.edit(
            note["id"],
            expected_revision=note["revision"],
            changes={"body": "Once"},
            author="agent",
            idempotency_key="same-edit",
        )
        second = self.store.edit(
            note["id"],
            expected_revision=note["revision"],
            changes={"body": "Once"},
            author="agent",
            idempotency_key="same-edit",
        )
        self.assertEqual(first["revision"], second["revision"])

    def test_backlinks_and_graph(self):
        target = self.create(title="Target", key="target")
        source = self.create(title="Source", body=f"See [[{target['id']}|Target]]", key="source")
        self.assertIn(source["id"], [item["id"] for item in self.store.get(target["id"])["backlinks"]])
        graph = self.store.graph(target["id"])
        self.assertTrue(any(edge["source"] == source["id"] and edge["target"] == target["id"] for edge in graph["edges"]))
        self.assertEqual({node["id"] for node in graph["nodes"]}, {source["id"], target["id"]})

    def test_rename_does_not_break_link(self):
        target = self.create(title="Old title", key="target")
        source = self.create(title="Source", body=f"[[{target['id']}]]", key="source")
        self.store.edit(
            target["id"],
            expected_revision=target["revision"],
            changes={"title": "New title"},
            author="user",
            idempotency_key="rename",
        )
        self.assertIn(source["id"], [item["id"] for item in self.store.get(target["id"])["backlinks"]])

    def test_history_diff_restore(self):
        note = self.create()
        first_revision = note["revision"]
        updated = self.store.edit(
            note["id"],
            expected_revision=first_revision,
            changes={"body": "Changed"},
            author="agent",
            idempotency_key="edit",
        )
        history = self.store.history(note["id"])
        self.assertGreaterEqual(len(history), 2)
        diff = self.store.diff(note["id"], first_revision, updated["revision"])
        self.assertIn("Changed", diff["diff"])
        restored = self.store.restore(
            note["id"],
            revision=first_revision,
            expected_revision=updated["revision"],
            author="user",
            idempotency_key="restore",
        )
        self.assertEqual(restored["body"], "Body")
        self.assertNotEqual(restored["revision"], first_revision)
        self.assertGreaterEqual(len(self.store.history(note["id"])), 3)

    def test_reindex_recovers_search_index(self):
        note = self.create(title="Único target")
        Path(self.tmp.name, "notes.db").unlink()
        self.store = NotesStore(self.tmp.name)
        found = self.store.list("Único", 10)
        self.assertEqual(found[0]["id"], note["id"])

    def test_external_file_change_is_detected(self):
        note = self.create()
        path = Path(self.tmp.name) / "notes" / f"{note['id']}.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("Body", "External"), encoding="utf-8")
        with self.assertRaises(StorageError):
            self.store.get(note["id"])

    def test_path_traversal_rejected(self):
        with self.assertRaises(NotesError):
            self.store.get("../secret")

    def test_crash_between_snapshot_and_note_write_can_retry_create(self):
        original = self.store._atomic_write
        def fail_note(path, text):
            if path.parent.name == "notes":
                raise RuntimeError("simulated crash before note publish")
            return original(path, text)
        self.store._atomic_write = fail_note
        with self.assertRaises(RuntimeError):
            self.create(key="crash-create")
        self.store._atomic_write = original
        retried = self.create(key="crash-create")
        self.assertEqual(retried["title"], "Project")
        self.assertEqual(retried["body"], "Body")

    def test_crash_between_snapshot_and_note_write_can_retry_edit(self):
        note = self.create()
        original = self.store._atomic_write
        def fail_note(path, text):
            if path.parent.name == "notes":
                raise RuntimeError("simulated crash before note publish")
            return original(path, text)
        self.store._atomic_write = fail_note
        with self.assertRaises(RuntimeError):
            self.store.edit(
                note["id"],
                expected_revision=note["revision"],
                changes={"body": "After crash"},
                author="agent",
                idempotency_key="crash-edit",
            )
        self.store._atomic_write = original
        retried = self.store.edit(
            note["id"],
            expected_revision=note["revision"],
            changes={"body": "After crash"},
            author="agent",
            idempotency_key="crash-edit",
        )
        self.assertEqual(retried["body"], "After crash")

    def test_index_failure_does_not_lose_published_note(self):
        original_index = self.store._index
        self.store._index = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("index down"))
        try:
            with self.assertRaises(RuntimeError):
                self.create()
        finally:
            self.store._index = original_index
        note = Path(self.tmp.name, "notes").glob("*.md")
        self.assertTrue(list(note))
        self.store.reindex()
        self.assertTrue(self.store.list("Project", 10))


if __name__ == "__main__":
    unittest.main(verbosity=2)
