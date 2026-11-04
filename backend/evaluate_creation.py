"""Opt-in real-model smoke test, isolated from user projects."""
import argparse
import io
import json
import os
from pathlib import Path
import tempfile
import time
from docx import Document
from dotenv import load_dotenv
from fastapi.testclient import TestClient


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--case',help='Run only the named scenario')
    parser.add_argument('--web',action='store_true',help='Enable real search/fetch for the selected case')
    parser.add_argument('--output',default='/tmp/office-agent-creation-evaluation.json')
    args=parser.parse_args()
    if not args.live: parser.error('--live required: uses paid model calls')
    load_dotenv(Path(__file__).resolve().parent.parent/'.env')
    from app.main import app
    report=[]
    with tempfile.TemporaryDirectory(prefix='office-create-eval-') as directory:
        os.environ['OFFICE_AGENT_DATA_DIR']=directory
        with TestClient(app) as client:
            project=client.post('/api/projects').json()['id']
            document=Document();document.add_heading('原始会议记录',0);document.add_paragraph('会议决定下周一完成产品演示，负责人是小李。')
            data=io.BytesIO();document.save(data)
            uploaded=client.post('/api/files',params={'project_id':project},files={'file':('会议记录.docx',data.getvalue())}).json()
            cases=[
                ('招聘报告',[],'我是五年经验的web前端开发，整理一下2026年北京招聘市场的这个岗位的招聘情况，并整理成一个文档。约1000字，包含表格；缺少可靠数据时明确说明，不编造统计数字。','create'),
                ('无附件新建',[],'生成一份团队会议准备清单Word文档，约300字，包含标题、列表和简单表格。','create'),
                ('有附件独立新建',[uploaded['id']],'另外新建一份读书计划Word文档，不参考或修改附件，约200字。','create'),
                ('参考附件新建',[uploaded['id']],'参考附件会议记录，另外创建一份行动清单Word文档，不修改原文件。','create'),
                ('明确修改',[uploaded['id']],'请将附件中的“小李”修改为“小王”，写入这个文件，其他内容不变。','edit'),
            ]
            for label,ids,prompt,expected in cases:
                if args.case and label != args.case: continue
                response=client.post('/api/tasks',json={'project_id':project,'file_ids':ids,'prompt':prompt,'web_enabled':args.web})
                response.raise_for_status(); task=response.json()
                deadline=time.monotonic()+160
                while task['status'] in ('queued','running','cancelling') and time.monotonic()<deadline:
                    time.sleep(.2);task=client.get('/api/tasks/'+task['id']).json()
                result=task.get('result') or {}
                passed=task['status']=='completed' and result.get('kind')==expected
                output=result.get('file')
                if output:
                    download=client.get('/api/files/'+output['id']+'/download')
                    parsed=Document(io.BytesIO(download.content))
                    passed=passed and bool(parsed.paragraphs) and client.get('/api/files/'+output['id']+'/content').status_code==200
                passed=passed and client.get('/api/files/'+uploaded['id']+'/download').content==data.getvalue()
                entry=dict(case=label,passed=passed,status=task['status'],kind=result.get('kind'),error=task.get('error'),creation_attempt=task.get('creation_attempt'),metrics=task.get('metrics'),text=result.get('text'),file=output)
                report.append(entry); print(json.dumps(entry,ensure_ascii=False),flush=True)
                Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
            if not all(row['passed'] for row in report): raise SystemExit(1)


if __name__=='__main__': main()
