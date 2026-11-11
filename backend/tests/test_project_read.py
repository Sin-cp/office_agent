"""A project file stays readable without being re-attached to the turn."""
import io
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
import test_files


class ProjectReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            OFFICE_AGENT_DATA_DIR=self.temp.name,
            OFFICE_AGENT_LOCAL_FS="0",
        )
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.project = self.client.post("/api/projects").json()["id"]
        self.file = self.client.post(
            f"/api/files?project_id={self.project}",
            files={
                "file": (
                    "已导入.docx",
                    io.BytesIO(test_files.docx()),
                    "application/octet-stream",
                )
            },
        ).json()

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

    def run_turn(self, prompt, scripted):
        with patch("app.model.complete", scripted):
            created = self.client.post(
                "/api/tasks", json={"project_id": self.project, "prompt": prompt}
            ).json()
            return self.wait(created["id"])

    def test_reads_a_project_file_without_attaching_it(self):
        state = {"n": 0}

        async def complete(messages, tools=None, timeout=45):
            state["n"] += 1
            if state["n"] == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "read_document",
                                "arguments": json.dumps({"file_id": self.file["id"]}),
                            },
                        }
                    ],
                }
            return {"content": "已读到正文。", "tool_calls": []}

        result = self.run_turn("总结一下这份文档", complete)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["result"]["text"], "已读到正文。")
        self.assertEqual([item["status"] for item in result["tools"]], ["ok"])
        self.assertIn("已导入.docx", result["tools"][0]["summary"])

    def test_foreign_file_is_rejected(self):
        other = self.client.post("/api/projects").json()["id"]
        foreign = self.client.post(
            f"/api/files?project_id={other}",
            files={
                "file": (
                    "别人.docx",
                    io.BytesIO(test_files.docx()),
                    "application/octet-stream",
                )
            },
        ).json()
        state = {"n": 0}

        async def complete(messages, tools=None, timeout=45):
            state["n"] += 1
            if state["n"] == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "read_document",
                                "arguments": json.dumps({"file_id": foreign["id"]}),
                            },
                        }
                    ],
                }
            return {"content": "读不到。", "tool_calls": []}

        result = self.run_turn("读一下那份文档", complete)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["tools"][0]["status"], "error")
        self.assertIn("不在当前项目", result["tools"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
