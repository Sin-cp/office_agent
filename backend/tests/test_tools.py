"""Every tool call is authorised before it can touch documents or the network."""
import json
import os
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import model, naming, tools


class FakeWeb:
    enabled = True

    def __init__(self):
        self.evidence = []
        self.fetched = []

    async def search(self, query):
        self.evidence.append(
            {
                "title": "标题",
                "url": "https://example.com/a",
                "content": "摘要内容",
                "published_at": None,
            }
        )
        return ["https://example.com/a"]

    async def fetch(self, url):
        self.fetched.append(url)
        self.evidence.append(
            {"title": "正文页", "url": url, "content": "网页正文", "truncated": False}
        )


def context(**overrides):
    inventory = {
        "id": "f1",
        "name": "测试.docx",
        "revision": "rev-1",
        "blocks": [{"id": "b1", "text": "第一段"}],
        "coverage": [],
    }

    async def load_inventory(file_id):
        return inventory

    async def list_sources():
        return [
            {
                "id": "f1",
                "name": "测试.docx",
                "available": True,
                "current_file": {"name": "测试.docx"},
            }
        ]

    values = dict(
        files=[{"id": "f1", "name": "测试.docx"}],
        load_inventory=load_inventory,
        list_sources=list_sources,
        web=None,
        allowed_files={"f1"},
        user_text="看看这份文档",
    )
    values.update(overrides)
    return tools.ToolContext(**values)


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_files(self):
        result = await tools.dispatch("list_files", "{}", context())
        self.assertTrue(result.ok)
        self.assertIn("测试.docx", result.content)

    async def test_read_document_reports_files_outside_the_project(self):
        async def load_inventory(file_id):
            raise tools.ToolError("forbidden", "该文件不在当前项目")

        result = await tools.dispatch(
            "read_document", '{"file_id":"other"}', context(load_inventory=load_inventory)
        )
        self.assertFalse(result.ok)
        self.assertIn("不在当前项目", result.summary)

    async def test_read_document_accepts_a_project_file_not_attached_this_turn(self):
        # Imported in an earlier turn: not in allowed_files, still readable.
        ctx = context(allowed_files=set())
        result = await tools.dispatch("read_document", '{"file_id":"f1"}', ctx)
        self.assertTrue(result.ok, result.summary)
        self.assertIn("测试.docx", result.summary)

    async def test_list_files_prefers_the_current_version(self):
        async def list_sources():
            return [
                {
                    "id": "src",
                    "name": "原名.docx",
                    "current_file": {"id": "cur", "name": "原名_修改版1.docx"},
                    "available": True,
                }
            ]

        result = await tools.dispatch("list_files", "{}", context(list_sources=list_sources))
        payload = json.loads(result.content)
        self.assertEqual(payload["files"][0]["id"], "cur")
        self.assertEqual(payload["files"][0]["name"], "原名_修改版1.docx")
        self.assertEqual(payload["files"][0]["source_id"], "src")

    async def test_read_document_returns_blocks(self):
        result = await tools.dispatch("read_document", '{"file_id":"f1"}', context())
        payload = json.loads(result.content)
        self.assertTrue(result.ok)
        self.assertEqual(len(payload["blocks"]), 1)
        self.assertFalse(payload["truncated"])
        self.assertIn("测试.docx", result.summary)

    async def test_read_document_oversized_block_is_rejected(self):
        async def load_inventory(file_id):
            return {
                "id": file_id,
                "name": "大文档.docx",
                "blocks": [{"id": "b1", "text": "长" * 20000}],
                "coverage": [],
            }

        result = await tools.dispatch(
            "read_document", '{"file_id":"f1"}', context(load_inventory=load_inventory)
        )
        self.assertFalse(result.ok)
        self.assertIn("分块预算", result.summary)

    async def test_read_document_chunk_pages_through_groups(self):
        blocks = [{"id": f"b{i}", "text": "x" * 10000, "kind": "paragraph"} for i in range(3)]

        async def load(file_id):
            return {
                "id": file_id,
                "name": "长文.docx",
                "revision": "r",
                "blocks": blocks,
                "coverage": [],
            }

        ctx = context(load_inventory=load)
        first = await tools.dispatch("read_document", json.dumps({"file_id": "f1"}), ctx)
        first_payload = json.loads(first.content)
        self.assertEqual(first_payload["chunk_start"], 1)
        self.assertEqual([block["id"] for block in first_payload["blocks"]], ["b0", "b1"])
        third = await tools.dispatch(
            "read_document", json.dumps({"file_id": "f1", "chunk": 3}), ctx
        )
        third_payload = json.loads(third.content)
        self.assertEqual(third_payload["chunk_start"], 3)
        self.assertEqual([block["id"] for block in third_payload["blocks"]], ["b2"])
        beyond = await tools.dispatch(
            "read_document", json.dumps({"file_id": "f1", "chunk": 4}), ctx
        )
        self.assertFalse(beyond.ok)
        self.assertIn("chunk_out_of_range", beyond.content)

    async def test_answer_documents_reads_the_selected_files(self):
        calls = []

        async def fake(question, inventories):
            calls.append((question, inventories))
            return "综合回答"

        with patch("app.model.answer_documents", fake):
            result = await tools.dispatch(
                "answer_documents", json.dumps({"question": "总结全文"}), context()
            )
        self.assertTrue(result.ok, result.summary)
        payload = json.loads(result.content)
        self.assertEqual(payload["answer"], "综合回答")
        self.assertEqual(calls[0][0], "总结全文")
        self.assertEqual(calls[0][1][0]["id"], "f1")

    async def test_search_requires_web(self):
        result = await tools.dispatch("search_web", '{"query":"天气"}', context())
        self.assertFalse(result.ok)
        self.assertIn("未开启联网", result.summary)

    async def test_search_returns_results_and_records_urls(self):
        session = FakeWeb()
        ctx = context(web=session)
        result = await tools.dispatch("search_web", '{"query":"最新消息"}', ctx)
        self.assertTrue(result.ok)
        self.assertEqual(len(json.loads(result.content)["results"]), 1)
        self.assertIn("https://example.com/a", ctx.search_urls)

    async def test_search_rejects_long_query(self):
        result = await tools.dispatch(
            "search_web", json.dumps({"query": "长" * 129}), context(web=FakeWeb())
        )
        self.assertFalse(result.ok)
        self.assertIn("1–128", result.summary)

    async def test_fetch_rejects_unknown_url(self):
        result = await tools.dispatch(
            "fetch_page", '{"url":"https://evil.test/x"}', context(web=FakeWeb())
        )
        self.assertFalse(result.ok)
        self.assertIn("只能读取", result.summary)

    async def test_fetch_allows_url_from_user_text(self):
        session = FakeWeb()
        ctx = context(web=session, user_text="看一下 https://example.com/help 这篇")
        result = await tools.dispatch(
            "fetch_page", '{"url":"https://example.com/help"}', ctx
        )
        self.assertTrue(result.ok)
        self.assertIn("网页正文", result.content)
        self.assertEqual(session.fetched, ["https://example.com/help"])

    async def test_fetch_requires_web_even_for_authorised_url(self):
        result = await tools.dispatch(
            "fetch_page", '{"url":"https://example.com/help"}', context(user_text="https://example.com/help")
        )
        self.assertFalse(result.ok)
        self.assertIn("未开启联网", result.summary)

    async def test_unknown_tool_is_reported(self):
        result = await tools.dispatch("drop_table", "{}", context())
        self.assertFalse(result.ok)
        self.assertIn("未知工具", result.summary)

    async def test_broken_arguments_do_not_raise(self):
        result = await tools.dispatch("read_document", "not json", context())
        self.assertFalse(result.ok)
        self.assertEqual(json.loads(result.content)["error"], "invalid_file")


class WriteToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_document_stages_an_artifact(self):
        record = {
            "id": "new",
            "name": "新文档.docx",
            "size": 10,
            "storage_key": "new.docx",
            "created_at": "2026-11-09T00:00:00Z",
            "source_file_id": None,
        }

        async def fake_generate(instruction, references, images=None):
            return SimpleNamespace(sections=[])

        def fake_prepare(root, project_id, content, images=None):
            return (Path("/tmp/a.tmp"), Path("/tmp/a.docx"), record)

        ctx = context(root=Path("/tmp"), project_id="p1")
        with patch("app.creation.generate", fake_generate), patch(
            "app.creation.prepare", fake_prepare
        ):
            result = await tools.dispatch(
                "create_document", '{"instruction":"生成一份报告"}', ctx
            )
        self.assertTrue(result.ok)
        self.assertIn("新文档.docx", result.summary)
        self.assertEqual(len(ctx.prepared), 1)
        self.assertEqual(ctx.artifacts[0]["kind"], "create")

    async def test_create_rejects_reference_outside_selection(self):
        ctx = context(root=Path("/tmp"), project_id="p1")
        result = await tools.dispatch(
            "create_document",
            '{"instruction":"x","reference_ids":["other"]}',
            ctx,
        )
        self.assertFalse(result.ok)
        self.assertIn("只能使用本轮附件", result.summary)

    async def test_edit_document_stages_a_version(self):
        item = (
            Path("/tmp/b.tmp"),
            Path("/tmp/b.docx"),
            {
                "id": "v2",
                "name": "测试_修改版1.docx",
                "size": 10,
                "storage_key": "b.docx",
                "created_at": "2026-11-09T00:00:00Z",
                "source_file_id": "f1",
            },
        )

        async def fake_run(prompt, inventories):
            return SimpleNamespace(
                kind="edit", text="已修改", operations=[object()], renames=[]
            )

        def fake_batch(root, file_id, revision, operations, name=None):
            return item, [{"op": "replace", "before": "a", "after": "b"}]

        ctx = context(root=Path("/tmp"), project_id="p1")
        with patch("app.edit_workflow.run", fake_run), patch(
            "app.documents.prepare_batch", fake_batch
        ):
            result = await tools.dispatch(
                "edit_document", '{"file_id":"f1","instruction":"改正式"}', ctx
            )
        self.assertTrue(result.ok)
        self.assertIn("修改 1 处", result.summary)
        self.assertEqual(ctx.artifacts[0]["kind"], "edit")
        self.assertEqual(len(ctx.artifacts[0]["changes"]), 1)

    async def test_edit_document_marks_a_renamed_artifact(self):
        item = (
            Path("/tmp/b.tmp"),
            Path("/tmp/b.docx"),
            {
                "id": "v2",
                "name": "新名字.docx",
                "size": 10,
                "storage_key": "b.docx",
                "created_at": "2026-11-09T00:00:00Z",
                "source_file_id": "f1",
            },
        )

        async def fake_run(prompt, inventories):
            return SimpleNamespace(
                kind="edit",
                text="已重命名",
                operations=[object()],
                renames=[naming.Rename(file_id="f1", name="新名字.docx")],
            )

        def fake_batch(root, file_id, revision, operations, name=None):
            return item, []

        ctx = context(root=Path("/tmp"), project_id="p1")
        with patch("app.edit_workflow.run", fake_run), patch(
            "app.documents.prepare_batch", fake_batch
        ):
            result = await tools.dispatch(
                "edit_document", '{"file_id":"f1","instruction":"改文件名"}', ctx
            )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(ctx.artifacts[0]["renamed"])

    async def test_rejected_reference_does_not_burn_the_only_slot(self):
        record = {
            "id": "new",
            "name": "新文档.docx",
            "size": 10,
            "storage_key": "new.docx",
            "created_at": "2026-11-09T00:00:00Z",
            "source_file_id": None,
        }

        async def fake_generate(instruction, references, images=None):
            return SimpleNamespace(sections=[])

        def fake_prepare(root, project_id, content, images=None):
            return (Path("/tmp/a.tmp"), Path("/tmp/a.docx"), record)

        ctx = context(root=Path("/tmp"), project_id="p1")
        with patch("app.creation.generate", fake_generate), patch(
            "app.creation.prepare", fake_prepare
        ):
            rejected = await tools.dispatch(
                "create_document",
                '{"instruction":"生成","reference_ids":["ghost"]}',
                ctx,
            )
            retried = await tools.dispatch(
                "create_document", '{"instruction":"生成"}', ctx
            )
        self.assertFalse(rejected.ok)
        self.assertTrue(retried.ok, retried.summary)
        self.assertEqual(len(ctx.prepared), 1)

    async def test_a_second_successful_create_is_rejected(self):
        record = {
            "id": "new",
            "name": "新文档.docx",
            "size": 10,
            "storage_key": "new.docx",
            "created_at": "2026-11-09T00:00:00Z",
            "source_file_id": None,
        }

        async def fake_generate(instruction, references, images=None):
            return SimpleNamespace(sections=[])

        def fake_prepare(root, project_id, content, images=None):
            return (Path("/tmp/a.tmp"), Path("/tmp/a.docx"), record)

        ctx = context(root=Path("/tmp"), project_id="p1")
        with patch("app.creation.generate", fake_generate), patch(
            "app.creation.prepare", fake_prepare
        ):
            first = await tools.dispatch("create_document", '{"instruction":"生成"}', ctx)
            second = await tools.dispatch("create_document", '{"instruction":"再来一份"}', ctx)
        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertIn("已生成过", second.summary)

    async def test_edit_document_rejects_a_second_edit(self):
        ctx = context(root=Path("/tmp"), project_id="p1")
        ctx.edited.add("f1")
        result = await tools.dispatch(
            "edit_document", '{"file_id":"f1","instruction":"再改"}', ctx
        )
        self.assertFalse(result.ok)
        self.assertIn("已修改过", result.summary)

    async def test_edit_document_passes_a_clarification_through(self):
        async def fake_run(prompt, inventories):
            return SimpleNamespace(
                kind="clarify", text="要改哪一段？", operations=[], renames=[]
            )

        ctx = context(root=Path("/tmp"), project_id="p1")
        with patch("app.edit_workflow.run", fake_run):
            result = await tools.dispatch(
                "edit_document", '{"file_id":"f1","instruction":"改"}', ctx
            )
        self.assertFalse(result.ok)
        self.assertIn("要改哪一段", result.summary)
        self.assertEqual(ctx.prepared, [])
        # A clarification must not consume the edit budget or lock the file.
        self.assertNotIn("f1", ctx.edited)
        self.assertNotIn("edit_document", ctx.calls)


class ImageToolTests(unittest.IsolatedAsyncioTestCase):
    def image_context(self, **overrides):
        async def load_image(image_id):
            return {"data": b"png-bytes", "mime": "image/png", "name": "截图.png"}

        values = dict(
            images=[{"id": "img1", "name": "截图.png", "mime": "image/png"}],
            allowed_images={"img1"},
            load_image=load_image,
        )
        values.update(overrides)
        return context(**values)

    async def test_analyze_image_returns_text(self):
        calls = []

        async def fake_vision(question, data, mime="image/png", timeout=60):
            calls.append((question, data, mime))
            return "表格：8月 98.78 元/月/㎡"

        with patch("app.model.vision", fake_vision):
            result = await tools.dispatch(
                "analyze_image", '{"image_id":"img1","question":"提取表格"}', self.image_context()
            )
        self.assertTrue(result.ok)
        self.assertIn("98.78", result.content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], "image/png")

    async def test_repeated_question_reuses_the_analysis(self):
        calls = []

        async def fake_vision(question, data, mime="image/png", timeout=60):
            calls.append(question)
            return "同一段文字"

        ctx = self.image_context()
        with patch("app.model.vision", fake_vision):
            await tools.dispatch("analyze_image", '{"image_id":"img1","question":"提取"}', ctx)
            second = await tools.dispatch(
                "analyze_image", '{"image_id":"img1","question":"提取"}', ctx
            )
        self.assertTrue(second.ok)
        self.assertEqual(len(calls), 1)

    async def test_image_outside_the_turn_is_rejected(self):
        result = await tools.dispatch(
            "analyze_image", '{"image_id":"other"}', self.image_context()
        )
        self.assertFalse(result.ok)
        self.assertIn("不在本轮", result.summary)

    async def test_missing_loader_is_reported(self):
        result = await tools.dispatch(
            "analyze_image", '{"image_id":"img1"}', self.image_context(load_image=None)
        )
        self.assertFalse(result.ok)
        self.assertIn("不支持读取图片", result.summary)


class AttachImageTests(unittest.IsolatedAsyncioTestCase):
    def image_context(self):
        async def load_image(image_id):
            return {"data": b"png", "mime": "image/png", "name": "图.png", "path": Path("/tmp/图.png")}

        return context(
            root=Path("/tmp"),
            project_id="p1",
            images=[{"id": "img1", "name": "图.png", "mime": "image/png"}],
            allowed_images={"img1"},
            load_image=load_image,
        )

    async def test_stages_an_insertion(self):
        item = (
            Path("/tmp/c.tmp"),
            Path("/tmp/c.docx"),
            {
                "id": "v3",
                "name": "测试_修改版2.docx",
                "size": 10,
                "storage_key": "c.docx",
                "created_at": "2026-11-09T00:00:00Z",
                "source_file_id": "f1",
            },
        )
        seen = {}

        def fake_prepare(root, file_id, after, path, width, caption):
            seen.update(after=after, width=width, caption=caption)
            return item

        ctx = self.image_context()
        with patch("app.documents.prepare_image", fake_prepare):
            result = await tools.dispatch(
                "attach_image",
                '{"image_id":"img1","file_id":"f1","after_block_id":"f1:p:2","width_cm":9,"caption":"图注"}',
                ctx,
            )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(seen["width"], 9)
        self.assertEqual(seen["caption"], "图注")
        self.assertEqual(len(ctx.prepared), 1)
        self.assertEqual(ctx.artifacts[0]["kind"], "edit")

    async def test_webp_is_rejected_for_documents(self):
        async def load_image(image_id):
            return {"data": b"webp", "mime": "image/webp", "name": "x.webp", "path": Path("/tmp/x.webp")}

        ctx = context(
            root=Path("/tmp"),
            project_id="p1",
            images=[{"id": "img1", "name": "x.webp", "mime": "image/webp"}],
            allowed_images={"img1"},
            load_image=load_image,
        )
        result = await tools.dispatch(
            "attach_image", '{"image_id":"img1","file_id":"f1"}', ctx
        )
        self.assertFalse(result.ok)
        self.assertIn("WebP", result.summary)

    async def test_rejects_an_image_outside_the_turn(self):
        result = await tools.dispatch(
            "attach_image", '{"image_id":"other","file_id":"f1"}', self.image_context()
        )
        self.assertFalse(result.ok)
        self.assertIn("不在本轮", result.summary)

    async def test_rejects_a_file_outside_the_selection(self):
        result = await tools.dispatch(
            "attach_image", '{"image_id":"img1","file_id":"other"}', self.image_context()
        )
        self.assertFalse(result.ok)
        self.assertIn("不在本轮选中范围", result.summary)


class LocalToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app import database, projects

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "uploads").mkdir()
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        database.initialize(self.root)
        with database.connection(self.root) as db:
            self.project = projects.create(db)
        self.env = patch.dict(
            os.environ,
            OFFICE_AGENT_LOCAL_FS="1",
            OFFICE_AGENT_LOCAL_ROOTS=self.temp.name,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def test_list_directory_requires_a_user_given_path(self):
        ctx = context(user_text="看看我的下载目录")
        result = await tools.dispatch(
            "list_directory", json.dumps({"path": str(self.root)}), ctx
        )
        self.assertFalse(result.ok)
        self.assertIn("只能统计用户消息里给出的路径", result.summary)

    async def test_list_directory_counts_files(self):
        (self.workspace / "a.txt").write_text("x")
        (self.workspace / "b.docx").write_text("x")
        ctx = context(user_text=f"看看 {self.workspace} 下有什么")
        result = await tools.dispatch(
            "list_directory", json.dumps({"path": str(self.workspace)}), ctx
        )
        self.assertTrue(result.ok, result.summary)
        payload = json.loads(result.content)
        self.assertEqual(payload["files"], 2)
        self.assertEqual(payload["by_extension"].get(".docx"), 1)

    async def test_import_local_docx_adds_it_to_the_selection(self):
        from docx import Document

        source = self.root / "本地.docx"
        Document().save(str(source))
        ctx = context(
            user_text=f"导入 {source} 并总结",
            root=self.root,
            project_id=self.project,
        )
        result = await tools.dispatch(
            "import_local_docx", json.dumps({"path": str(source)}), ctx
        )
        self.assertTrue(result.ok, result.summary)
        payload = json.loads(result.content)
        self.assertEqual(len(payload["files"]), 1)
        file_id = payload["files"][0]["file_id"]
        self.assertIn(file_id, ctx.allowed_files)
        self.assertTrue(any(item["id"] == file_id for item in ctx.files))

    async def test_import_directory_imports_top_level_docx(self):
        from docx import Document

        for name in ("a.docx", "b.docx"):
            Document().save(str(self.workspace / name))
        (self.workspace / "c.txt").write_text("x")
        imported = []
        ctx = context(
            user_text=f"导入 {self.workspace}",
            root=self.root,
            project_id=self.project,
            on_import=lambda record: imported.append(record["id"]),
        )
        result = await tools.dispatch(
            "import_local_docx", json.dumps({"path": str(self.workspace)}), ctx
        )
        self.assertTrue(result.ok, result.summary)
        payload = json.loads(result.content)
        self.assertEqual(len(payload["files"]), 2)
        self.assertEqual(len(ctx.local_sources), 2)
        self.assertEqual(len(imported), 2)
        self.assertEqual(len(ctx.files), 3)
        self.assertEqual(payload["total"], 2)

    async def test_second_local_edit_is_rejected(self):
        from docx import Document

        source = self.root / "本地.docx"
        Document().save(str(source))
        ctx = context(
            user_text=f"导入 {source} 并修改",
            root=self.root,
            project_id=self.project,
        )
        imported = await tools.dispatch(
            "import_local_docx", json.dumps({"path": str(source)}), ctx
        )
        file_id = json.loads(imported.content)["files"][0]["file_id"]
        ctx.local_edit_used = True
        result = await tools.dispatch(
            "edit_document", json.dumps({"file_id": file_id, "instruction": "改"}), ctx
        )
        self.assertFalse(result.ok)
        self.assertIn("本地修改版", result.summary)

    async def test_import_rejects_a_path_the_user_did_not_give(self):
        from docx import Document

        source = self.root / "隐藏.docx"
        Document().save(str(source))
        ctx = context(user_text="导入一份文件", root=self.root, project_id=self.project)
        result = await tools.dispatch(
            "import_local_docx", json.dumps({"path": str(source)}), ctx
        )
        self.assertFalse(result.ok)
        self.assertIn("只能导入用户消息里给出的路径", result.summary)


if __name__ == "__main__":
    unittest.main()
