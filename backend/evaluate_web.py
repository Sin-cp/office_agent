"""Opt-in live web smoke test; synthetic prompts only, no application data is read."""
import argparse
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
from app import model, web


async def evaluate(output):
    report = []
    cases = [('greeting', '你好', True), ('weather', '北京今天天气怎么样？请核对今天的日期和天气数据更新时间。', True),
             ('article', '请读取并简要总结 https://api-docs.deepseek.com/guides/search/', True),
             ('disabled', '北京今天天气怎么样？', False)]
    for name, prompt, enabled in cases:
        async def progress(label):
            print(f'{name}: {label}', flush=True)
        session = web.WebSession(enabled, progress)
        token = web.session_context.set(session)
        context_token = model.conversation_context.set({})
        metrics = []
        metric_token = model.call_metrics.set(metrics)
        print(f'{name}: 开始', flush=True)
        try:
            result = await model.respond_project(prompt, [])
            report.append(dict(case=name, answer=result.text, **session.result_fields(), web_metrics=session.metrics,
                               model_metrics=metrics, searches=session.searches, fetches=session.fetches))
            print(json.dumps({k:v for k,v in report[-1].items() if k in ('case','web_status','web_notices','searches','fetches')},ensure_ascii=False),flush=True)
        except model.ModelError as exc:
            report.append(dict(case=name, error=str(exc)))
            print(f'{name}: {exc}', flush=True)
        finally:
            web.session_context.reset(token)
            model.conversation_context.reset(context_token)
            model.call_metrics.reset(metric_token)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'报告已保存：{output}', flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Calls configured paid APIs using synthetic prompts')
    parser.add_argument('--output',type=Path,default=Path('/tmp/office-agent-web-evaluation.json'))
    args=parser.parse_args()
    if not args.live:
        parser.error('真实调用需显式传入 --live')
    load_dotenv(Path(__file__).resolve().parents[1]/'.env',override=False)
    asyncio.run(evaluate(args.output))
