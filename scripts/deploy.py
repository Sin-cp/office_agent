"""Local deployment coordinator. No cloud SDK, stored credentials or Git commit needed."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
FILES = (
    'compose.yaml', 'backend/Dockerfile', 'backend/.dockerignore', 'backend/requirements.txt',
    'frontend/Dockerfile', 'frontend/.dockerignore', 'frontend/package.json',
    'frontend/package-lock.json', 'frontend/index.html', 'frontend/tsconfig.json',
    'frontend/vite.config.ts', 'frontend/nginx.conf',
)
FRONTEND_SUFFIXES = {'.vue', '.ts', '.tsx', '.js', '.jsx', '.css', '.scss', '.sass', '.less',
                     '.json', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico',
                     '.woff', '.woff2', '.ttf'}
# Excluded receiver entries are protected from rsync --delete as well.
EXCLUDES = ('.*', '__pycache__', 'node_modules', 'dist', 'data', 'uploads', 'outputs',
            '*.db', '*.db-*', '*.sqlite*', '*.log', '*.pyc', '*.pem', '*.key')
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15',
               '-o', 'ServerAliveCountMax=3']


class DeployError(Exception):
    pass


def valid_path(value):
    path = PurePosixPath(value)
    if (not value.startswith('/') or '..' in path.parts or len(path.parts) < 4
            or any(ord(c) < 32 for c in value) or path.name in {'data', 'uploads', 'backend', 'frontend', 'src'}):
        raise argparse.ArgumentTypeError('项目目录必须是专用绝对路径，例如 /srv/office-agent/app，不能指向根目录或数据目录')
    return str(path)


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise DeployError(f'运行文件缺失或为符号链接：{path.relative_to(ROOT)}')


def stage_files(destination):
    for name in FILES:
        source = ROOT / name
        regular(source)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for folder, suffixes in [('backend/app', {'.py'}), ('frontend/src', FRONTEND_SUFFIXES)]:
        source = ROOT / folder
        if source.is_symlink() or not source.is_dir():
            raise DeployError(f'源码目录缺失或为符号链接：{folder}')
        (destination / folder).mkdir(parents=True)
        for directory, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {'__pycache__', 'node_modules', 'dist', 'data', 'uploads', 'outputs'}]
            for name in dirs:
                if (Path(directory) / name).is_symlink():
                    raise DeployError(f'源码目录中不允许符号链接：{Path(directory) / name}')
            for name in files:
                path = Path(directory) / name
                if name.startswith('.') or path.suffix not in suffixes:
                    continue
                regular(path)
                target = destination / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)


def sync(stage, host, remote, dry_run):
    common = ['rsync', '-rtp', '--chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r', '--itemize-changes',
              '-e', shlex.join(['ssh', *SSH_OPTIONS])]
    if dry_run:
        common.append('--dry-run')
    for folder, suffixes in [('backend/app', {'.py'}), ('frontend/src', FRONTEND_SUFFIXES)]:
        filters = [f'--exclude={pattern}' for pattern in EXCLUDES]
        filters += ['--include=*/', *[f'--include=*{suffix}' for suffix in sorted(suffixes)], '--exclude=*']
        command = [*common, '--delete', *filters, str(stage / folder) + '/',
                   host + ':' + shlex.quote(remote + '/' + folder + '/')]
        subprocess.run(command, check=True)
    for name in FILES:
        command = [*common, str(stage / name), host + ':' + shlex.quote(remote + '/' + name)]
        subprocess.run(command, check=True)


def main():
    if sys.version_info < (3, 8):
        print('需要 Python 3.8 或更新版本。', file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description='通过 SSH 同步当前工作区并部署前后端；不上传本地配置和业务数据。')
    parser.add_argument('--host', default='root@60.205.124.226', help='SSH 用户@主机或 ~/.ssh/config 别名')
    parser.add_argument('--remote-dir', type=valid_path, default='/srv/office-agent/app')
    parser.add_argument('--backup-dir', default='/srv/office-agent/backups')
    parser.add_argument('--dry-run', action='store_true', help='只执行只读预检及 rsync 预览')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', args.host):
        parser.error('--host 必须是 SSH 别名或用户@主机（端口和密钥请在 SSH config 中配置）')
    for tool in ('ssh', 'rsync'):
        if not shutil.which(tool):
            parser.error(f'本地缺少 {tool}')
    remote_script = Path(__file__).with_name('deploy_remote.py').read_text(encoding='utf-8')
    settings = json.dumps(dict(app=args.remote_dir, backups=args.backup_dir, dry_run=args.dry_run))
    command = ['ssh', *SSH_OPTIONS, args.host, shlex.join(['python3', '-u', '-c', remote_script, settings])]
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix='office-agent-deploy-') as directory:
            stage = Path(directory)
            stage_files(stage)
            print(f"{'预览' if args.dry_run else '部署'}目标：{args.host}:{args.remote_dir}", flush=True)
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            ready = False
            complete = False
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError as exc:
                    raise DeployError('SSH 返回了非预期内容，请检查远程登录脚本是否向 stdout 输出文字') from exc
                if event['event'] == 'ready':
                    if ready:
                        raise DeployError('收到重复部署许可')
                    ready = True
                    sync(stage, args.host, event['app'], args.dry_run)
                    process.stdin.write('sync_complete\n')
                    process.stdin.flush()
                    process.stdin.close()
                elif event['event'] == 'done':
                    complete = True
                    print(event['message'], flush=True)
                elif event['event'] == 'error':
                    raise DeployError(event['message'])
                else:
                    print(event['message'], flush=True)
            if process.wait() != 0 or not complete:
                raise DeployError('远程部署未成功完成，请查看上方日志')
    except (DeployError, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as exc:
        print(f'部署停止：{exc}', file=sys.stderr)
        print(f'可登录 {args.host}，进入 {args.remote_dir} 后执行 docker compose ps 和 docker compose logs --tail=100 backend web。', file=sys.stderr)
        return 1
    finally:
        if process is not None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()  # EOF aborts the remote sync phase and releases its lock.
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    return 0


if __name__ == '__main__':
    sys.exit(main())
