"""The loop owns ordering, budget, cancellation and the event stream."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

from app import agent, tools


def context(**overrides):
    async def load_inventory(file_id):
        return {"id": file_id, "name": "测试.docx", "blocks": [{"id": "b1", "text": "正文"}], "coverage": []}

    async def list_sources():
        return [{"id": "f1", "name": "测试.docx", "available": True, "current_file": {"name": "测试.docx"}}]

    values = dict(
        files=[{"id": "f1", "name": "测试.docx"}],
        load_inventory=load_inventory,
        list_sources=list_sources,
        web=None,
        allowed_files={"f1"},
        user_text="帮我看看这份文档",
    )
    values.update(overrides)
    return tools.ToolContext(**values)


def call(name, arguments, identity="call-1"):
    return {
        "id": identity,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, replies, **kwargs):
        captured = []
        scripted = list(replies)

        async def complete(messages, tools=None, timeout=45):
            captured.append(json.loads(json.dumps(messages)))
            return scripted.pop(0)

        with patch("app.model.complete", complete):
            result = await agent.run("帮我看看这份文档", context=context(), **kwargs)
        return result, captured

    async def test_answers_without_tools(self):
        result, _ = await self.run_loop([{"content": "直接回答", "tool_calls": []}])
        self.assertEqual(result.text, "直接回答")
        self.assertEqual(result.tools, [])

    async def test_runs_a_tool_then_answers(self):
        ok = tools.ToolResult(True, "项目内 1 份文档", '{"files":[]}')
        replies = [
            {"content": "", "tool_calls": [call("list_files", {})]},
            {"content": "看完了", "tool_calls": []},
        ]
        with patch("app.tools.dispatch", return_value=ok):
            result, messages = await self.run_loop(replies)
        self.assertEqual(result.text, "看完了")
        self.assertEqual([event["status"] for event in result.tools], ["ok"])
        self.assertEqual(result.tools[0]["label"], "查看项目文件")
        # assistant tool_calls + tool result must be replayed to the model
        self.assertEqual(messages[1][-2]["role"], "assistant")
        self.assertEqual(messages[1][-1]["role"], "tool")

    async def test_failed_tool_is_skipped(self):
        bad = tools.ToolResult(False, "网页读取失败", '{"error":"timeout"}')
        replies = [
            {"content": "", "tool_calls": [call("fetch_page", {"url": "https://x.test"})]},
            {"content": "没查到，改用其他方式", "tool_calls": []},
        ]
        with patch("app.tools.dispatch", return_value=bad):
            result, _ = await self.run_loop(replies)
        self.assertEqual(result.tools[0]["status"], "error")
        self.assertIn("没查到", result.text)

    async def test_three_consecutive_failures_stop_the_loop(self):
        bad = tools.ToolResult(False, "失败", "{}")
        replies = [
            {"content": "", "tool_calls": [call("fetch_page", {"url": f"https://x.test/{i}"}, f"c{i}")]}
            for i in range(3)
        ]
        with patch("app.tools.dispatch", return_value=bad):
            result, _ = await self.run_loop(replies)
        self.assertIn("连续三个工具步骤失败", result.text)
        self.assertEqual(len(result.tools), 3)

    async def test_step_budget_stops_at_eight(self):
        replies = [
            {"content": "", "tool_calls": [call("list_files", {}, f"c{i}")]} for i in range(agent.MAX_TOOL_STEPS)
        ]
        ok = tools.ToolResult(True, "好的", "{}")
        with patch("app.tools.dispatch", return_value=ok):
            result, _ = await self.run_loop(replies)
        self.assertIn("工具步数上限", result.text)
        self.assertEqual(len(result.tools), agent.MAX_TOOL_STEPS)

    async def test_cancel_between_steps(self):
        async def complete(messages, tools=None, timeout=45):
            return {"content": "", "tool_calls": [call("list_files", {})]}

        with patch("app.model.complete", complete), patch(
            "app.tools.dispatch", return_value=tools.ToolResult(True, "ok", "{}")
        ):
            with self.assertRaises(asyncio.CancelledError):
                await agent.run("看看文档", context=context(), cancelled=lambda: True)

    async def test_events_are_announced_twice(self):
        seen = []

        async def on_event(event):
            seen.append(dict(event))

        ok = tools.ToolResult(True, "项目内 1 份文档", "{}")
        replies = [
            {"content": "", "tool_calls": [call("list_files", {})]},
            {"content": "完成", "tool_calls": []},
        ]
        with patch("app.tools.dispatch", return_value=ok):
            await self.run_loop(replies, on_event=on_event)
        self.assertEqual([event["status"] for event in seen], ["running", "ok"])

    async def test_write_artifact_decides_result_kind(self):
        async def fake_dispatch(name, arguments, ctx):
            ctx.artifacts.append({"file_id": "n1", "name": "新.docx", "kind": "create", "changes": []})
            return tools.ToolResult(True, "已生成《新.docx》", "{}")

        replies = [
            {"content": "", "tool_calls": [call("create_document", {"instruction": "写一份"})]},
            {"content": "已生成文件", "tool_calls": []},
        ]
        with patch("app.tools.dispatch", fake_dispatch):
            result, _ = await self.run_loop(replies)
        self.assertEqual(result.kind, "create")
        self.assertEqual(result.text, "已生成文件")

    async def test_empty_answer_is_rejected(self):
        with self.assertRaises(agent.LoopError):
            await self.run_loop([{"content": "   ", "tool_calls": []}])


class PromptTests(unittest.TestCase):
    def test_local_rules_follow_the_switch(self):
        with patch.dict(os.environ, {"OFFICE_AGENT_LOCAL_FS": "1"}):
            self.assertIn("import_local_docx", agent.system_prompt())
        with patch.dict(os.environ, {"OFFICE_AGENT_LOCAL_FS": "0"}):
            self.assertNotIn("import_local_docx", agent.system_prompt())

    def test_loop_prompt_never_mentions_local_when_disabled(self):
        with patch.dict(os.environ, {"OFFICE_AGENT_LOCAL_FS": "0"}):
            self.assertNotIn("本机路径", agent.system_prompt())

    def test_local_capability_and_overwrite_follow_the_switches(self):
        with patch.dict(
            os.environ,
            {"OFFICE_AGENT_LOCAL_FS": "1", "OFFICE_AGENT_LOCAL_WRITE": "1"},
        ):
            prompt = agent.system_prompt()
            self.assertIn("目录", prompt)
            self.assertIn("覆盖源文件", prompt)
            self.assertIn("能不能读取本地文件", prompt)
        with patch.dict(
            os.environ,
            {"OFFICE_AGENT_LOCAL_FS": "1", "OFFICE_AGENT_LOCAL_WRITE": "0"},
        ):
            prompt = agent.system_prompt()
            self.assertIn("覆盖源文件功能当前已关闭", prompt)


if __name__ == "__main__":
    unittest.main()


class WiringTests(unittest.IsolatedAsyncioTestCase):
    """Manager.run_agent must hand the loop a locked-down context."""

    class FakeManager:
        root = None

        def __init__(self):
            self.cancel_requested = False

        async def checkpoint(self, state, stage):
            state["stage"] = stage

    async def test_run_agent_builds_context_and_keeps_events(self):
        from app import model, tasks

        seen = {}

        async def fake_run(prompt, **kwargs):
            seen.update(kwargs)
            seen["prompt"] = prompt
            return agent.AgentResult(text="回答", tools=kwargs["events"])

        async def load_inventory(file):
            return {"id": file["id"], "name": file["name"], "blocks": [], "coverage": []}

        state = {
            "prompt": "看看文档",
            "project_id": "p1",
            "files": [{"id": "f1", "name": "a.docx"}],
            "tools": [],
            "stage": 0,
        }
        with patch("app.agent.run", fake_run):
            response = await tasks.Manager.run_agent(
                self.FakeManager(), state, {"summary": ""}, None, load_inventory, [], {}
            )
        self.assertEqual(response.kind, "answer")
        self.assertEqual(response.text, "回答")
        self.assertEqual(seen["context"].allowed_files, {"f1"})
        self.assertEqual(seen["context"].user_text, "看看文档")
        self.assertEqual(seen["events"], [])
