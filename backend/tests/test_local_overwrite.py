"""Local import → edit → confirmed overwrite flows through the tool loop."""

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from fastapi.testclient import TestClient

from app import agent, model, operations, tools
from app.main import app
import loop_support


class LocalOverwriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.home = base / "home"
        self.data = base / "data"
        for directory in (self.home, self.data):
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
        self.client = TestClient(app)
        self.client.__enter__()
        self.project = self.client.post("/api/projects").json()["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def source(self, name="本地.docx", text="原始内容"):
        path = self.home / name
        document = Document()
        document.add_paragraph(text)
        document.save(str(path))
        return path

    def wait(self, identity):
        for _ in range(400):
            state = self.client.get("/api/tasks/" + identity).json()
            if state["status"] not in ("queued", "running", "cancelling"):
                return state
            time.sleep(0.01)
        self.fail("Task timed out")

    def run_turn(self, prompt, handler):
        with loop_support.patch_agent(handler):
            response = self.client.post(
                "/api/tasks",
                json={"project_id": self.project, "file_ids": [], "prompt": prompt},
            )
            self.assertEqual(response.status_code, 202, response.text)
            return self.wait(response.json()["id"])

    def edit_after_import(self, path, text="新增一句", between=None):
        async def handler(prompt, context):
            imported = await tools.dispatch(
                "import_local_docx", json.dumps({"path": str(path)}), context
            )
            self.assertTrue(imported.ok, imported.summary)
            payload = json.loads(imported.content)
            if between is not None:
                await asyncio.to_thread(between, payload)
            for entry in payload["files"][:1]:
                inventory = await context.load_inventory(entry["file_id"])
                block = inventory["blocks"][0]
                operation = operations.Operation(
                    op="insert_paragraph",
                    target=block["id"],
                    expected_text=block["text"],
                    text=text,
                )
                response = model.Response(
                    kind="edit", text="已修改", operations=[operation]
                )
                with patch("app.edit_workflow.run", return_value=response):
                    edited = await tools.dispatch(
                        "edit_document",
                        json.dumps(
                            {
                                "file_id": entry["file_id"],
                                "instruction": "在末尾加一句",
                            }
                        ),
                        context,
                    )
                self.assertTrue(edited.ok, edited.summary)
            return agent.AgentResult(text="已修改", kind="edit")

        return self.run_turn(f"修改 {path}，在末尾加一句", handler)

    def test_overwrite_writes_source_and_keeps_backup(self):
        path = self.source()
        state = self.edit_after_import(path)
        self.assertEqual(state["status"], "completed", state)
        local = state["result"]["local_edit"]
        self.assertEqual(Path(local["path"]).resolve(), path.resolve())
        self.assertEqual(local["name"], "本地.docx")
        self.assertEqual(len(state["result"]["outputs"]), 1)
        self.assertIn("未改动", state["result"]["text"])

        response = self.client.post(f"/api/tasks/{state['id']}/overwrite-local")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(Path(body["path"]).resolve(), path.resolve())
        self.assertTrue(Path(body["backup"]).is_file())
        written = Document(str(path))
        self.assertEqual([p.text for p in written.paragraphs], ["原始内容", "新增一句"])

        again = self.client.post(f"/api/tasks/{state['id']}/overwrite-local")
        self.assertEqual(again.status_code, 409)
        task = self.client.get(f"/api/tasks/{state['id']}").json()
        self.assertTrue(task["result"]["local_edit"]["overwritten"]["backup"])

    def test_source_changed_after_import_hides_overwrite(self):
        path = self.source()

        def change(payload):
            document = Document()
            document.add_paragraph("已被外部修改")
            document.save(str(path))

        state = self.edit_after_import(path, between=change)
        self.assertEqual(state["status"], "completed", state)
        self.assertNotIn("local_edit", state["result"])
        self.assertIn("已变化", state["result"]["text"])

    def test_write_switch_blocks_overwrite(self):
        path = self.source()
        state = self.edit_after_import(path)
        self.assertIn("local_edit", state["result"])
        with patch.dict(os.environ, {"OFFICE_AGENT_LOCAL_WRITE": "0"}):
            response = self.client.post(f"/api/tasks/{state['id']}/overwrite-local")
        self.assertEqual(response.status_code, 403)

    def test_directory_import_and_single_overwrite(self):
        folder = self.home / "docs"
        folder.mkdir()
        self.source("docs/一.docx", "第一篇")
        self.source("docs/二.docx", "第二篇")
        state = self.edit_after_import(folder)
        self.assertEqual(state["status"], "completed", state)
        self.assertEqual(len(state["files"]), 2)
        self.assertEqual(
            len(self.client.get(f"/api/projects/{self.project}/files").json()["items"]),
            2,
        )
        local = state["result"]["local_edit"]
        self.assertTrue(local["path"].endswith("一.docx"))
        response = self.client.post(f"/api/tasks/{state['id']}/overwrite-local")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [p.text for p in Document(local["path"]).paragraphs],
            ["第一篇", "新增一句"],
        )

    def test_import_without_edit_has_no_overwrite_notice(self):
        path = self.source()

        async def handler(prompt, context):
            imported = await tools.dispatch(
                "import_local_docx", json.dumps({"path": str(path)}), context
            )
            self.assertTrue(imported.ok, imported.summary)
            return agent.AgentResult(text="已导入")

        state = self.run_turn(f"导入 {path} 并总结", handler)
        self.assertEqual(state["status"], "completed", state)
        self.assertNotIn("local_edit", state["result"])
        self.assertNotIn("已变化", state["result"]["text"])
