"""Opt-in live evaluation using generated fixtures, never user-uploaded documents."""
import argparse
import io
import json
import os
from pathlib import Path
import tempfile
import time
from docx import Document
from fastapi.testclient import TestClient
from app.main import app


def fixture():
    document = Document()
    document.add_heading('项目计划', 1)
    document.add_paragraph('本项目于六月启动，目标是提升文档整理效率。')
    document.add_heading('实施步骤', 2)
    document.add_paragraph('首先整理资料，然后完成初稿。')
    table = document.add_table(rows=2, cols=2)
    table.style = 'Table Grid'
    for row, values in zip(table.rows, [('事项', '状态'), ('验收', '未完成')]):
        for cell, value in zip(row.cells, values):
            cell.text = value
    document.sections[0].header.paragraphs[0].text = '页眉必须保留'
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly enable billed provider calls')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error('必须提供 --live 才会调用真实模型')
    report = []
    with tempfile.TemporaryDirectory(prefix='office-agent-eval-') as directory:
        previous = os.environ.get('OFFICE_AGENT_DATA_DIR')
        os.environ['OFFICE_AGENT_DATA_DIR'] = directory
        try:
            with TestClient(app) as client:
                file = client.post('/api/files', files={'file': ('evaluation.docx', fixture(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}).json()
                cases = [
                    ('summary', '请总结文档，包括表格中的状态。仅回答，不修改文件。', 'answer'),
                    ('preference', '此文档后续统一使用正式、简洁的表达。请确认，不修改文件。', 'answer'),
                    ('context_edit', '沿用刚才的表达要求，将实施步骤的正文改写得更正式。', 'edit'),
                    ('table', '将表格中验收的状态从未完成改为已完成。', 'edit'),
                    ('clarify', '把某个地方改一下，但我还没有说明具体位置和改法，请先问我。', 'clarify'),
                ]
                for name, prompt, expected in cases:
                    before = client.get('/api/files').json()['total']
                    response = client.post('/api/tasks', json={'file_id': file['id'], 'prompt': prompt})
                    response.raise_for_status()
                    identity = response.json()['id']
                    deadline = time.monotonic() + 960
                    while True:
                        state = client.get('/api/tasks/' + identity).json()
                        if state['status'] not in ('queued', 'running', 'cancelling'):
                            break
                        if time.monotonic() > deadline:
                            client.post('/api/tasks/' + identity + '/cancel')
                            raise TimeoutError('Evaluation task timed out')
                        time.sleep(.25)
                    result = state.get('result') or {}
                    after = client.get('/api/files').json()['total']
                    report.append(dict(case=name, expected_kind=expected, actual_kind=result.get('kind'), status=state['status'],
                        passed=state['status']=='completed' and result.get('kind')==expected and after-before==(1 if expected=='edit' else 0),
                        files_added=after-before, elapsed_seconds=state['elapsed_seconds'], metrics=state.get('metrics', []),
                        error=state.get('error'), manual_quality_review_required=True))
                    if result.get('file'):
                        file = result['file']
        finally:
            if previous is None:
                os.environ.pop('OFFICE_AGENT_DATA_DIR', None)
            else:
                os.environ['OFFICE_AGENT_DATA_DIR'] = previous
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{sum(item["passed"] for item in report)}/{len(report)} structural checks passed. Report: {args.output}')


if __name__ == '__main__':
    main()
