"""Local filesystem helpers: root policy, listing, import and confirmed write-back."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_files
from app import database, local_fs, projects


class LocalFsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.home = base / "home"
        self.outside = base / "outside"
        self.data = base / "data"
        for directory in (self.home, self.outside, self.data):
            directory.mkdir()
        self.env = patch.dict(
            os.environ,
            HOME=str(self.home),
            OFFICE_AGENT_DATA_DIR=str(self.data),
            OFFICE_AGENT_LOCAL_FS="1",
            OFFICE_AGENT_LOCAL_WRITE="1",
            OFFICE_AGENT_LOCAL_ROOTS="",
        )
        self.env.start()
        database.initialize(self.data)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def docx(self, path: Path, text="测试"):
        path.write_bytes(test_files.docx())
        return path

    def test_resolve_enforces_roots_and_type(self):
        inside = self.docx(self.home / "a.docx")
        outside = self.docx(self.outside / "b.docx")
        self.assertEqual(local_fs.resolve_path(str(inside), "file"), inside.resolve())
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.resolve_path(str(outside), "file")
        self.assertEqual(caught.exception.status, 403)
        rel = Path("relative.docx")
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.resolve_path(str(rel))
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.resolve_path(str(self.home / "missing.docx"))
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.resolve_path(str(inside), "dir")
        self.assertEqual(caught.exception.status, 400)

    def test_symlink_escape_is_rejected(self):
        outside = self.docx(self.outside / "b.docx")
        link = self.home / "link.docx"
        link.symlink_to(outside)
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.resolve_path(str(link), "file")
        self.assertEqual(caught.exception.status, 403)

    def test_quoted_path_is_accepted(self):
        target = self.docx(self.home / "带 空格.docx")
        self.assertEqual(
            local_fs.resolve_path(f"'{target}'", "file"), target.resolve()
        )

    def test_list_directory_counts_extensions(self):
        folder = self.home / "docs"
        folder.mkdir()
        (folder / "sub").mkdir()
        (folder / "a.docx").write_bytes(b"a")
        (folder / "b.DOCX").write_bytes(b"b")
        (folder / "c.py").write_text("print(1)")
        (folder / "README").write_text("x")
        result = local_fs.list_directory(folder)
        self.assertEqual(result["files"], 4)
        self.assertEqual(result["dirs"], 1)
        self.assertEqual(result["by_extension"][".docx"], 2)
        self.assertEqual(result["by_extension"][".py"], 1)
        self.assertEqual(result["by_extension"]["(无后缀)"], 1)
        self.assertIn("4 个文件", result["summary_text"])
        (folder / "sub" / "nested.docx").write_bytes(b"n")
        self.assertEqual(local_fs.list_directory(folder, recursive=True)["files"], 5)
        self.assertEqual(local_fs.list_directory(folder)["files"], 4)

    def test_collect_docx_supports_directory_file_and_limit(self):
        folder = self.home / "docs"
        folder.mkdir()
        for name in ("b.docx", "a.docx", "note.txt"):
            (folder / name).write_bytes(b"x")
        found, total = local_fs.collect_docx(folder)
        self.assertEqual([item.name for item in found], ["a.docx", "b.docx"])
        self.assertEqual(total, 2)
        found, total = local_fs.collect_docx(folder, limit=1)
        self.assertEqual(len(found), 1)
        self.assertEqual(total, 2)
        single = self.docx(self.home / "one.docx")
        self.assertEqual(local_fs.collect_docx(single), ([single], 1))
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.collect_docx(folder / "note.txt")
        self.assertEqual(caught.exception.status, 400)

    def test_import_copies_bytes_and_registers_project(self):
        source = self.docx(self.home / "测试 文档.docx")
        with database.connection(self.data) as db:
            project_id = projects.create(db)
        record = local_fs.import_docx(self.data, project_id, source)
        stored = self.data / "uploads" / record["storage_key"]
        self.assertEqual(stored.read_bytes(), source.read_bytes())
        self.assertEqual(record["name"], "测试 文档.docx")
        self.assertIsNotNone(database.get(self.data, record["id"]))
        with database.connection(self.data) as db:
            row = db.execute(
                "SELECT 1 FROM project_sources WHERE project_id=? AND source_file_id=?",
                (project_id, record["id"]),
            ).fetchone()
        self.assertIsNotNone(row)

    def test_import_rejects_invalid_docx(self):
        bad = self.home / "bad.docx"
        bad.write_bytes(b"not a zip")
        with database.connection(self.data) as db:
            project_id = projects.create(db)
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.import_docx(self.data, project_id, bad)
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(list((self.data / "uploads").glob("*")), [])

    def test_write_in_place_keeps_backup(self):
        target = self.docx(self.home / "修改.docx")
        original = target.read_bytes()
        identity = local_fs.file_identity(target)
        backup = local_fs.write_in_place(target, b"new bytes", identity)
        self.assertEqual(target.read_bytes(), b"new bytes")
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(backup.parent, target.resolve().parent)
        self.assertIn(".bak-", backup.name)

    def test_write_in_place_rejects_changed_or_replaced_file(self):
        target = self.docx(self.home / "修改.docx")
        identity = local_fs.file_identity(target)
        target.write_bytes(b"changed elsewhere")
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.write_in_place(target, b"new bytes", identity)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(target.read_bytes(), b"changed elsewhere")

    def test_write_in_place_rejects_outside_root(self):
        target = self.docx(self.outside / "b.docx")
        identity = local_fs.file_identity(target)
        with self.assertRaises(local_fs.LocalError) as caught:
            local_fs.write_in_place(target, b"new bytes", identity)
        self.assertEqual(caught.exception.status, 403)
        self.assertNotEqual(target.read_bytes(), b"new bytes")

    def test_restore_puts_backup_back(self):
        target = self.docx(self.home / "修改.docx")
        original = target.read_bytes()
        backup = local_fs.write_in_place(target, b"new bytes", local_fs.file_identity(target))
        local_fs.restore(backup, target)
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue(backup.exists())

    def test_switches_disable_access(self):
        target = self.docx(self.home / "a.docx")
        with patch.dict(os.environ, OFFICE_AGENT_LOCAL_FS="0"):
            with self.assertRaises(local_fs.LocalError) as caught:
                local_fs.resolve_path(str(target))
            self.assertEqual(caught.exception.status, 403)
        with patch.dict(os.environ, OFFICE_AGENT_LOCAL_WRITE="0"):
            with self.assertRaises(local_fs.LocalError) as caught:
                local_fs.write_in_place(target, b"x", local_fs.file_identity(target))
            self.assertEqual(caught.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
