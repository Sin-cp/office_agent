import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database, projects
import test_files
import test_history
from test_documents import example


class ProjectTests(unittest.TestCase):
    setUp = test_files.FilesTest.setUp
    tearDown = test_files.FilesTest.tearDown
    upload = test_files.FilesTest.upload
    run_task = test_history.HistoryTests.run_task
    history = test_history.HistoryTests.history

    def listing(self):
        return self.client.get('/api/projects').json()

    def test_versions_source_and_independent_same_name(self):
        content = example()
        source = self.upload(content).json()
        first = self.run_task(source)['result']['file']
        latest = self.run_task(first)['result']['file']
        project = self.listing()['items'][0]
        self.assertEqual(self.listing()['total'], 1)
        self.assertEqual(project['source_file_id'], source['id'])
        self.assertEqual(project['name'], '修改正文')
        self.assertEqual(project['created_at'], source['created_at'])
        self.assertEqual(self.history(project)['current_file']['id'], latest['id'])
        self.assertEqual(self.client.get(f"/api/files/{source['id']}/download").content, content)
        for file in [first, latest]:
            self.assertEqual(self.client.get(f"/api/files/{file['id']}/download").status_code, 200)
        self.upload(example())
        self.assertEqual(self.listing()['total'], 2)
        pages = [self.client.get(f'/api/projects?limit=1&offset={i}').json() for i in range(2)]
        self.assertNotEqual(pages[0]['items'][0]['id'], pages[1]['items'][0]['id'])

    def test_migration_missing_source_and_unknown_ancestry(self):
        source = self.upload(example()).json()
        version = self.run_task(source)['result']['file']
        self.client.delete(f"/api/files/{source['id']}")
        root = Path(self.temp.name)
        with database.connection(root) as db:
            db.execute('DROP TABLE project_sources')
            db.execute('DROP TABLE projects')
        database.initialize(root)
        project = self.listing()['items'][0]
        self.assertFalse(project['source_available'])
        self.assertEqual(project['name'], '修改正文')
        self.assertEqual(project['source_file_id'], source['id'])
        self.assertEqual(self.history(project)['current_file']['id'], version['id'])
        database.initialize(root)
        self.assertEqual(self.listing()['total'], 1)
        with database.connection(root) as db:
            db.execute('DELETE FROM tasks')
            db.execute('DROP TABLE project_sources')
            db.execute('DROP TABLE projects')
            db.execute('DROP TABLE file_history')
        database.initialize(root)
        project = self.listing()['items'][0]
        self.assertFalse(project['source_available'])
        self.assertNotEqual(project['source_file_id'], version['id'])
        self.assertIn('源文件不可用', project['name'])

    def test_whole_project_delete_isolated_and_durable(self):
        source = self.upload(example()).json()
        version = self.run_task(source)['result']['file']
        other = self.upload(example()).json()
        self.assertEqual(self.client.delete(f"/api/projects/{source['id']}").status_code, 200)
        root = Path(self.temp.name)
        database.initialize(root)
        projects.recover_deletions(root)
        self.assertEqual(self.listing()['total'], 1)
        self.assertEqual(self.listing()['items'][0]['id'], other['id'])
        for file in [source, version]:
            self.assertEqual(self.client.get(f"/api/files/{file['id']}/download").status_code, 404)
            self.assertEqual(self.client.get(f"/api/files/{file['id']}/history").status_code, 404)
        with database.connection(root) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)
        self.assertEqual(self.client.delete('/api/projects/missing').status_code, 404)

    def test_database_failure_restores_all_bytes(self):
        source = self.upload(example()).json()
        version = self.run_task(source)['result']['file']
        root = Path(self.temp.name)
        before = {p.name: p.read_bytes() for p in (root/'uploads').iterdir()}
        with database.connection(root) as db:
            db.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON projects BEGIN SELECT RAISE(ABORT,'test'); END")
        self.assertEqual(self.client.delete(f"/api/projects/{source['id']}").status_code, 500)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (root/'uploads').iterdir()})
        self.assertEqual(self.history(source)['current_file']['id'], version['id'])
        self.assertEqual(self.listing()['total'], 1)

    def test_partial_move_failure_restores(self):
        source = self.upload(example()).json()
        self.run_task(source)
        root = Path(self.temp.name)
        before = {p.name: p.read_bytes() for p in (root/'uploads').iterdir()}
        original = Path.replace
        count = 0
        def replace(path, target):
            nonlocal count
            if path.parent.resolve() == (root/'uploads').resolve():
                count += 1
                if count == 2:
                    raise OSError('test move failure')
            return original(path, target)
        with patch.object(Path, 'replace', replace):
            self.assertEqual(self.client.delete(f"/api/projects/{source['id']}").status_code, 500)
        self.assertEqual(before, {p.name:p.read_bytes() for p in (root/'uploads').iterdir()})

    def test_restart_recovery_before_and_after_commit(self):
        source = self.upload(example()).json()
        root = Path(self.temp.name)
        key = source['id'] + '.docx'
        stage = root/'.project-trash'/'interrupted'
        stage.mkdir(parents=True)
        (stage/'manifest.json').write_text(json.dumps(dict(project_id=source['id'],storage_keys=[key])))
        (root/'uploads'/key).replace(stage/key)
        projects.recover_deletions(root)
        self.assertTrue((root/'uploads'/key).exists())
        self.assertFalse(stage.exists())
        stage.mkdir()
        (stage/'manifest.json').write_text(json.dumps(dict(project_id='deleted',storage_keys=[key])))
        (stage/key).write_bytes(b'committed trash')
        projects.recover_deletions(root)
        self.assertFalse(stage.exists())
        self.assertTrue((root/'uploads'/key).exists())

    def test_active_task_blocks_project_delete(self):
        source = self.upload(example()).json()
        root = Path(self.temp.name)
        with database.connection(root) as db:
            db.execute('INSERT INTO tasks VALUES (?,?)', ('active', json.dumps(dict(status='cancelling'))))
        self.assertEqual(self.client.delete(f"/api/projects/{source['id']}").status_code, 409)
        with database.connection(root) as db:
            db.execute("DELETE FROM tasks WHERE id='active'")


    def test_delete_source_preserves_chat_outputs_and_stays_deleted_on_restart(self):
        source = self.upload(example()).json()
        output = self.run_task(source)['result']['file']
        project_id = source['id']
        other = self.client.post(f'/api/files?project_id={project_id}', files={'file': ('other.docx', example())}).json()
        before = self.client.get(f'/api/projects/{project_id}/messages').json()
        endpoint = f"/api/projects/{project_id}/files/{source['id']}"
        self.assertEqual(self.client.delete(endpoint).status_code, 200)
        self.assertEqual(self.client.get(f"/api/files/{source['id']}/download").status_code, 404)
        self.assertEqual(self.client.get(f"/api/files/{output['id']}/download").status_code, 200)
        self.assertEqual(self.client.get(f'/api/projects/{project_id}/messages').json(), before)
        database.initialize(Path(self.temp.name))
        sources = self.client.get(f'/api/projects/{project_id}/files').json()['items']
        self.assertEqual([f['id'] for f in sources], [other['id']])
        self.assertEqual(self.client.delete(endpoint).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/projects/{project_id}/files/{other['id']}").status_code, 200)
        self.assertEqual(self.listing()['items'][0]['file_count'], 0)
        self.assertEqual(self.listing()['total'], 1)
        self.assertEqual(self.client.delete(f'/api/projects/{project_id}').status_code, 200)
        self.assertEqual(self.client.get(f"/api/files/{output['id']}/download").status_code, 404)

    def test_delete_source_failure_rolls_back_and_checks_project(self):
        source = self.upload(example()).json()
        endpoint = f"/api/projects/{source['id']}/files/{source['id']}"
        self.assertEqual(self.client.delete(f"/api/projects/other/files/{source['id']}").status_code, 404)
        with patch.object(Path, 'unlink', side_effect=OSError('disk error')):
            self.assertEqual(self.client.delete(endpoint).status_code, 500)
        self.assertEqual(self.listing()['items'][0]['file_count'], 1)
        self.assertEqual(self.client.get(f"/api/files/{source['id']}/download").status_code, 200)
        with database.connection(Path(self.temp.name)) as db:
            db.execute("INSERT INTO tasks(id,payload) VALUES (?,?)", ('busy', json.dumps({'status':'running'})))
        self.assertEqual(self.client.delete(endpoint).status_code, 409)
