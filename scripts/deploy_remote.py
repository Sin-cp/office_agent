"""Executed via SSH python -c; stdout is JSON protocol, subprocess logs use stderr."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
from uuid import uuid4


class Failure(Exception):
    pass


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)


def note(message):
    emit('progress', message=message)


def run(args, capture=False):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE if capture else sys.stderr,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        # Captured inspect/config output may include secrets. Never print it on failure.
        raise Failure(f'命令失败（退出码 {result.returncode}）：{args[0]} {args[1] if len(args)>1 else ""}')
    return result.stdout.strip() if capture else ''


def within(path, parent):
    return path == parent or parent in path.parents


def safe_path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or len(path.parts) < 4 or any(ord(c)<32 for c in value):
        raise Failure('部署和备份目录必须是专用的绝对路径')
    return path.resolve()


def container_probe(container, mode):
    code = '''import json, os, sqlite3
from pathlib import Path
root=Path(os.environ.get('OFFICE_AGENT_DATA_DIR','/data'))
if root.resolve() != Path('/data'):
    raise SystemExit('后端数据路径不是 /data，停止部署')
if MODE == 'permissions':
    for path, flags in [(root,os.W_OK|os.X_OK),(root/'uploads',os.W_OK|os.X_OK),(root/'office_agent.db',os.R_OK|os.W_OK)]:
        if not path.exists() or not os.access(path,flags):
            raise SystemExit('后端用户无权访问或写入 '+str(path)+'；请检查属主、权限及挂载')
else:
    db=sqlite3.connect((root/'office_agent.db').as_uri()+'?mode=ro',uri=True,timeout=10)
    try:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone()
        count=db.execute("SELECT COUNT(*) FROM tasks WHERE json_extract(payload,'$.status') IN ('queued','running','cancelling')").fetchone()[0] if exists else 0
        if count: raise SystemExit('有文档任务正在执行，请等待完成或停止后再部署')
    finally: db.close()
'''.replace('MODE', repr(mode))
    run(['docker', 'exec', container, 'python', '-c', code])


def deploy(settings):
    app = safe_path(settings['app'])
    backups = safe_path(settings['backups'])
    dry_run = settings['dry_run']
    if app.name in {'data', 'uploads', 'backend', 'frontend', 'src'} or not app.is_dir():
        raise Failure('项目目录无效或不存在；本脚本用于更新已有部署')
    for name in ['.env', 'compose.yaml', 'backend', 'frontend', 'backend/app', 'frontend/src']:
        path = app / name
        if path.is_symlink() or not path.exists() or not within(path.resolve(), app):
            raise Failure(f'目标缺失或含符号链接：{name}')
    # Guard each managed file as well as managed directory parents.
    for name in ['backend/Dockerfile', 'backend/.dockerignore', 'backend/requirements.txt',
                 'frontend/Dockerfile', 'frontend/.dockerignore', 'frontend/package.json',
                 'frontend/package-lock.json', 'frontend/index.html', 'frontend/tsconfig.json',
                 'frontend/vite.config.ts', 'frontend/nginx.conf']:
        if (app / name).is_symlink():
            raise Failure(f'目标文件不能是符号链接：{name}')
    for folder in ['backend/app', 'frontend/src']:
        if any(p.is_symlink() for p in (app / folder).rglob('*')):
            raise Failure(f'{folder} 内含符号链接，请先核对目录')
    if within(backups, app) or within(app, backups):
        raise Failure('备份目录不能包含项目目录或位于项目目录内')
    for tool in ['docker', 'rsync', 'tar']:
        if not shutil.which(tool):
            raise Failure(f'服务器缺少 {tool}')
    os.chdir(app)
    compose = ['docker', 'compose']
    run([*compose, 'version'], capture=True)
    lock = None
    try:
        if not dry_run:
            lock_path = app.parent / ('.' + app.name + '.deploy.lock')
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            lock = os.fdopen(fd, 'w')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Failure('已有部署正在运行，请稍后再试') from exc
        run([*compose, 'config', '--quiet'])
        original_config = json.loads(run([*compose, 'config', '--format', 'json'], capture=True))
        backend = run([*compose, 'ps', '-q', 'backend'], capture=True)
        web = run([*compose, 'ps', '-q', 'web'], capture=True)
        if not backend or not web or '\n' in backend or '\n' in web:
            raise Failure('需要各一个运行中的 backend/web 容器，请先恢复现有部署')
        mounts = json.loads(run(['docker', 'inspect', '--format', '{{json .Mounts}}', backend], capture=True))
        mounted = [m for m in mounts if m['Destination'] == '/data']
        if len(mounted) != 1 or mounted[0]['Type'] != 'bind' or not mounted[0].get('RW'):
            raise Failure('/data 必须是可写的宿主机绑定挂载')
        data = Path(mounted[0]['Source']).resolve()
        managed = [app / 'backend', app / 'frontend']
        if not data.is_dir() or within(app, data) or any(within(data, p) for p in managed):
            raise Failure('数据目录与源码目录重叠或不可用')
        if within(backups, data) or within(data, backups):
            raise Failure('备份目录与业务数据目录不能互相包含')
        container_probe(backend, 'permissions')
        container_probe(backend, 'tasks')
        emit('ready', app=str(app))
        if sys.stdin.readline().strip() != 'sync_complete':
            raise Failure('同步未完成，未构建或重启服务')
        if dry_run:
            emit('done', message='预览完成：未写入服务器文件、未获取部署锁、未构建或重启。')
            return
        # Validate incoming configuration without leaking expanded environment values.
        run([*compose, 'config', '--quiet'])
        config = json.loads(run([*compose, 'config', '--format', 'json'], capture=True))
        if config.get('name') != original_config.get('name'):
            raise Failure('Compose 项目名发生变化，停止自动部署')
        backend_config = config['services']['backend']
        volumes = [v for v in backend_config.get('volumes', []) if v.get('target') == '/data']
        if (len(volumes) != 1 or volumes[0].get('type') != 'bind'
                or Path(volumes[0]['source']).resolve() != data or volumes[0].get('read_only')
                or backend_config.get('ports')):
            raise Failure('新配置改变了数据挂载或公开了后端端口，停止自动部署')
        note('正在构建后端镜像；现有容器保持运行。')
        run([*compose, 'build', 'backend'])
        note('正在构建前端镜像；现有容器保持运行。')
        run([*compose, 'build', 'web'])
        container_probe(backend, 'permissions')
        container_probe(backend, 'tasks')
        backups.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(backups.stat().st_mode) & 0o077:
            raise Failure('备份目录权限过宽，请先设置为仅目录所有者可访问（0700）')
        archive = backups / ('data-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8] + '.tar.gz')
        partial = archive.with_suffix('.partial')
        note('暂停入口，复查活动任务，然后停止后端并备份数据。')
        stopped = False
        try:
            stopped = True
            run(['docker', 'stop', web])
            # Close ingress before the final check so another browser cannot start a task.
            container_probe(backend, 'tasks')
            run(['docker', 'stop', backend])
            old_umask = os.umask(0o077)
            try:
                run(['tar', '-czf', str(partial), '-C', str(data), '.'])
                run(['tar', '-tzf', str(partial)], capture=True)
                partial.rename(archive)
            finally:
                os.umask(old_umask)
        except BaseException:
            partial.unlink(missing_ok=True)
            if stopped:
                note('尚未更新容器，尝试启动原容器恢复服务。')
                try:
                    run(['docker', 'start', backend, web])
                except Failure:
                    note('原容器恢复失败，请检查 docker ps -a 及容器日志。')
            raise
        note(f'数据备份已完成：{archive}')
        try:
            run([*compose, 'up', '-d', '--wait', '--wait-timeout', '120'])
            raw = run([*compose, 'exec', '-T', 'web', 'wget', '-qO-', 'http://127.0.0.1/api/health'], capture=True)
            if json.loads(raw).get('status') != 'ok':
                raise Failure('代理健康接口未返回 ok')
            new_backend = run([*compose, 'ps', '-q', 'backend'], capture=True)
            container_probe(new_backend, 'permissions')
        except BaseException:
            note(f'更新未通过验证；保留备份 {archive}。不要直接降级数据库；请查看容器日志，按部署文档恢复。')
            raise
        ports = run([*compose, 'port', 'web', '80'], capture=True)
        emit('done', message=f'部署成功：容器健康及 web → backend 代理检查通过。监听地址：{ports}（公网访问需使用服务器地址；回环绑定请使用 SSH 隧道）。备份：{archive}')
    finally:
        if lock is not None:
            lock.close()  # Keep the lock inode: unlinking permits concurrent holders.


def interrupted(signum, frame):
    raise Failure('SSH 会话中断或部署被终止')


def main():
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        deploy(json.loads(sys.argv[1]))
    except (Exception, KeyboardInterrupt) as exc:
        emit('error', message=str(exc))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
