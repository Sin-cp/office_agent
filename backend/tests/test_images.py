"""Images are validated by content, stored outside the version family, and scoped."""
import base64
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import images
from app.main import app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


class DetectTests(unittest.TestCase):
    def test_formats_come_from_magic_bytes(self):
        self.assertEqual(images.detect_image(PNG), ("png", "image/png"))
        self.assertEqual(images.detect_image(JPEG), ("jpg", "image/jpeg"))
        self.assertEqual(images.detect_image(GIF), ("gif", "image/gif"))
        self.assertEqual(images.detect_image(WEBP), ("webp", "image/webp"))

    def test_text_and_empty_are_rejected(self):
        self.assertIsNone(images.detect_image(b"hello world, not an image"))
        self.assertIsNone(images.detect_image(b""))

    def test_names_are_flattened_and_extended(self):
        self.assertEqual(images._clean_name("../../evil", "png"), "evil.png")
        self.assertEqual(images._clean_name("截图 2026", "png"), "截图 2026.png")
        self.assertEqual(images._clean_name("", "jpg"), "图片.jpg")


class ImageApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, OFFICE_AGENT_DATA_DIR=self.temp.name)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.project = self.client.post("/api/projects").json()["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def upload(self, data: bytes, name="shot.png", project=None):
        return self.client.post(
            f"/api/projects/{project or self.project}/images",
            files={"file": (name, io.BytesIO(data), "application/octet-stream")},
        )

    def test_upload_list_and_download(self):
        response = self.upload(PNG, "微信截图.png")
        self.assertEqual(response.status_code, 201, response.text)
        asset = response.json()
        self.assertEqual(asset["mime"], "image/png")
        self.assertTrue(asset["name"].endswith(".png"))

        listing = self.client.get(f"/api/projects/{self.project}/images").json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["id"], asset["id"])

        binary = self.client.get(f"/api/images/{asset['id']}/download")
        self.assertEqual(binary.status_code, 200)
        self.assertEqual(binary.content, PNG)

    def test_content_type_does_not_decide_the_format(self):
        response = self.upload(JPEG, "photo.txt", )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["mime"], "image/jpeg")

    def test_non_image_is_rejected(self):
        response = self.upload(b"just text", "notes.txt")
        self.assertEqual(response.status_code, 400)

    def test_empty_and_oversized_are_rejected(self):
        self.assertEqual(self.upload(b"", "empty.png").status_code, 400)
        big = PNG + b"\x00" * (5 * 1024 * 1024)
        self.assertEqual(self.upload(big, "big.png").status_code, 413)

    def test_unknown_project_and_missing_image(self):
        self.assertEqual(self.upload(PNG, project="missing").status_code, 404)
        self.assertEqual(self.client.get("/api/images/nope/download").status_code, 404)


class ImageTaskTests(unittest.TestCase):
    """A turn with images must reach the model as content blocks."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, OFFICE_AGENT_DATA_DIR=self.temp.name)
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.project = self.client.post("/api/projects").json()["id"]
        asset = self.client.post(
            f"/api/projects/{self.project}/images",
            files={"file": ("shot.png", io.BytesIO(PNG), "image/png")},
        ).json()
        self.asset = asset

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def wait(self, identity):
        for _ in range(200):
            state = self.client.get("/api/tasks/" + identity).json()
            if state["status"] not in ("queued", "running", "cancelling"):
                return state
            time.sleep(0.02)
        self.fail("Task timed out")

    def test_turn_sends_image_blocks_to_the_model(self):
        captured = {}

        async def complete(messages, tools=None, timeout=45):
            captured["messages"] = messages
            return {"content": "这是一张测试图片。", "tool_calls": []}

        with patch("app.model.complete", complete):
            created = self.client.post(
                "/api/tasks",
                json={
                    "project_id": self.project,
                    "prompt": "看看这张图",
                    "image_ids": [self.asset["id"]],
                },
            ).json()
            state = self.wait(created["id"])
        self.assertEqual(state["status"], "completed", state)
        self.assertEqual(state["result"]["text"], "这是一张测试图片。")
        content = captured["messages"][1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_image_from_another_project_is_rejected(self):
        other = self.client.post("/api/projects").json()["id"]
        response = self.client.post(
            "/api/tasks",
            json={"project_id": other, "prompt": "看看", "image_ids": [self.asset["id"]]},
        )
        self.assertEqual(response.status_code, 409)


class InsertTests(unittest.TestCase):
    """P4: deterministic insertion keeps the text and adds one picture."""

    setUp = ImageApiTests.setUp
    tearDown = ImageApiTests.tearDown
    upload = ImageApiTests.upload

    def test_insert_image_after_a_block(self):
        import test_files
        from docx import Document

        from app import documents, operations

        uploaded = self.client.post(
            f"/api/files?project_id={self.project}",
            files={"file": ("doc.docx", io.BytesIO(test_files.docx()), "application/octet-stream")},
        ).json()
        asset = self.upload(PNG).json()
        root = Path(self.temp.name)
        png_path = images.load_asset(root, asset["id"])["path"]
        loaded = documents.load_file(root, uploaded["id"])
        blocks, _ = operations.inventory(loaded[3], uploaded["id"])
        temporary, _, record = documents.prepare_image(
            root, uploaded["id"], blocks[0]["id"], png_path, 8, "图注"
        )
        try:
            document = Document(str(temporary))
            self.assertEqual(len(document.inline_shapes), 1)
            self.assertIn("图注", [p.text for p in document.paragraphs])
            self.assertIn("测试文档", document.paragraphs[0].text)
            self.assertEqual(record["source_file_id"], uploaded["id"])
        finally:
            temporary.unlink(missing_ok=True)

    def test_unknown_anchor_is_rejected(self):
        from fastapi import HTTPException

        import test_files

        from app import documents

        uploaded = self.client.post(
            f"/api/files?project_id={self.project}",
            files={"file": ("doc.docx", io.BytesIO(test_files.docx()), "application/octet-stream")},
        ).json()
        asset = self.upload(PNG).json()
        root = Path(self.temp.name)
        png_path = images.load_asset(root, asset["id"])["path"]
        with self.assertRaises(HTTPException):
            documents.prepare_image(root, uploaded["id"], "missing:block", png_path)


if __name__ == "__main__":
    unittest.main()
