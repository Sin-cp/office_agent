"""Run: python -m unittest discover -s tests -v (no listening port needed)."""

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import sqlite3

from fastapi.testclient import TestClient
from app.main import app
from app.storage import MAX_BYTES


def docx(macro=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>测试文档</w:t></w:r></w:p></w:body></w:document>',
        )
        if macro:
            archive.writestr("word/vbaProject.bin", b"macro")
    return stream.getvalue()


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, OFFICE_AGENT_DATA_DIR=self.temp.name)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def upload(self, content=None, name="中文 测试.docx"):
        return self.client.post(
            "/api/files", files={"file": (name, docx() if content is None else content)}
        )

    def test_roundtrip_same_name_and_restart(self):
        original = docx()
        first = self.upload(original).json()
        second = self.upload().json()
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotIn("storage_key", first)
        result = self.client.get("/api/files").json()
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["items"][0]["id"], second["id"])
        self.assertEqual(
            self.client.get("/api/files?limit=1&offset=1").json()["items"][0]["id"],
            first["id"],
        )
        self.client.__exit__(None, None, None)
        self.client = TestClient(app)
        self.client.__enter__()
        self.assertEqual(self.client.get("/api/files").json()["total"], 2)
        response = self.client.get(f"/api/files/{first['id']}/download")
        self.assertEqual(response.content, original)
        self.assertIn("filename*=utf-8''", response.headers["content-disposition"])
        self.assertEqual(len(list((Path(self.temp.name) / "uploads").iterdir())), 2)

    def test_invalid_files_leave_no_records_or_bytes(self):
        for content, name, status in [
            (b"", "empty.docx", 400),
            (b"fake", "fake.docx", 400),
            (docx(), "test.doc", 400),
            (docx(True), "macro.docx", 400),
            (b"x" * (MAX_BYTES + 1), "large.docx", 413),
            (docx()[:-30], "broken.docx", 400),
        ]:
            with self.subTest(name=name):
                self.assertEqual(self.upload(content, name).status_code, status)
        self.assertEqual(self.client.get("/api/files").json()["total"], 0)
        self.assertEqual(list((Path(self.temp.name) / "uploads").iterdir()), [])

    def test_storage_rollback(self):
        with (
            self.assertLogs("app.files", level="ERROR"),
            patch(
                "app.database.insert",
                side_effect=sqlite3.OperationalError("test failure"),
            ),
        ):
            self.assertEqual(self.upload().status_code, 500)
        self.assertEqual(self.client.get("/api/files").json()["total"], 0)
        self.assertEqual(list((Path(self.temp.name) / "uploads").iterdir()), [])

    def test_paths_pagination_and_missing_files(self):
        self.assertEqual(
            self.client.get("/api/files/not-an-id/download").status_code, 404
        )
        self.assertEqual(
            self.client.get(
                "/api/files/00000000-0000-0000-0000-000000000000/download"
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get("/api/files?limit=0").status_code, 422)
        record = self.upload(name="../../safe.docx").json()
        self.assertEqual(record["name"], "safe.docx")
        (Path(self.temp.name) / "uploads" / f"{record['id']}.docx").unlink()
        self.assertEqual(
            self.client.get(f"/api/files/{record['id']}/download").status_code, 404
        )

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").json()["version"], "0.2.0")


if __name__ == "__main__":
    unittest.main()
