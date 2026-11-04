"""Real-provider project edit check. Synthetic content and isolated storage only."""
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
    parser.add_argument('--output',default='/tmp/office-agent-edit-repair.json')
    args=parser.parse_args()
    if not args.live: parser.error('--live is required; calls the paid model API')
    load_dotenv(Path(__file__).resolve().parent.parent/'.env')
    from app.main import app
    from app import model, edit_workflow
    from unittest.mock import patch
    plans=[]
    original_call=model.call
    async def tracked_call(system,content,schema=None):
        result=await original_call(system,content,schema)
        if schema is edit_workflow.Plan: plans.append(result.model_dump())
        return result
    with tempfile.TemporaryDirectory(prefix='office-edit-repair-') as directory:
        os.environ['OFFICE_AGENT_DATA_DIR']=directory
        with TestClient(app) as client, patch('app.model.call',side_effect=tracked_call):
            project=client.post('/api/projects').json()['id']
            doc=Document();doc.add_heading('2027年元旦西安三日游攻略',0)
            doc.add_paragraph('住宿在西安市中心。三天均从酒店出发并返回酒店，只安排市内和周边景点。')
            for title,text in [
                ('第一天：钟楼与回民街','乘地铁至钟楼，钟楼至鼓楼可步行串联，随后步行至回民街，晚间坐地铁回酒店。'),
                ('第二天：华清宫与兵马俑','乘地铁9号线至华清池站后换乘公交至华清宫，下午乘公交去兵马俑，晚上返回酒店。'),
                ('第三天：大雁塔与大唐不夜城','上午乘地铁去大雁塔，下午游玩周边，晚上步行游览大唐不夜城后返回酒店。')]:
                doc.add_heading(title,1);doc.add_paragraph(text)
            table=doc.add_table(rows=1,cols=2);table.style='Table Grid'
            table.cell(0,0).text='行程';table.cell(0,1).text='交通方式'
            for day,transport in [('第一天','优先地铁'),('第二天','地铁9号线换乘公交'),('第三天','地铁与步行')]:
                cells=table.add_row().cells;cells[0].text=day;cells[1].text=transport
            data=io.BytesIO();doc.save(data);original=data.getvalue()
            file=client.post('/api/files',params={'project_id':project},files={'file':('西安元旦三日游攻略.docx',original)}).json()
            prompt='我的意思是：我改成自己开车去游玩，在这个文档的基础上改成自驾游攻略（包括景点停车等）。不需要跨城往返路线；未经核实的停车信息请明确标注。'
            response=client.post('/api/tasks',json={'project_id':project,'file_ids':[file['id']],'prompt':prompt,'web_enabled':False})
            response.raise_for_status();state=response.json();deadline=time.monotonic()+600
            last=None
            while state['status'] in ('queued','running','cancelling') and time.monotonic()<deadline:
                state=client.get('/api/tasks/'+state['id']).json()
                activity=state.get('activity')
                if activity!=last: print(activity,flush=True);last=activity
                time.sleep(.3)
            output=(state.get('result') or {}).get('file')
            extracted=[]
            if output:
                data=client.get('/api/files/'+output['id']+'/download').content
                parsed=Document(io.BytesIO(data))
                extracted=[p.text for p in parsed.paragraphs]+[' | '.join(c.text for c in row.cells) for t in parsed.tables for row in t.rows]
            passed=(state['status']=='completed' and (state.get('result') or {}).get('kind')=='edit'
                    and bool(output) and client.get('/api/files/'+file['id']+'/download').content==original)
            report=dict(passed=passed,status=state['status'],error=state.get('error'),repair_round=state.get('repair_round'),
                        plans=plans,text=(state.get('result') or {}).get('text'),metrics=state.get('metrics'),contents=extracted)
            Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps({k:v for k,v in report.items() if k not in ('contents','metrics')},ensure_ascii=False),flush=True)
            if not passed: raise SystemExit(1)


if __name__=='__main__': main()
