"""Offline integration tests. All SSH/rsync/Docker/tar commands are fakes in a temp PATH."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deploy', SCRIPTS / 'deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

FAKE = r'''#!/usr/bin/env python3
import fnmatch,json,os,shlex,shutil,subprocess,sys,time
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as stream:
    # Never log the inline source or environment; mimic external command metadata.
    stream.write(json.dumps([name, *(['remote-session'] if name=='ssh' else args)])+'\n')
fail=os.environ.get('FAIL','')
if name=='ssh':
    if fail=='ssh':sys.exit(255)
    sys.exit(subprocess.call(shlex.split(args[-1])))
if name=='rsync':
    if fail=='sync':sys.exit(23)
    source=Path(args[-2]);target=Path(shlex.split(args[-1].split(':',1)[1])[0])
    if '--dry-run' in args:sys.exit(0)
    if source.is_dir():
        target.mkdir(parents=True,exist_ok=True)
        # Match the relevant whitelist used by the real invocation.
        suffixes={arg.split('*',1)[1] for arg in args if arg.startswith('--include=*.')}
        for file in target.rglob('*'):
            relative=file.relative_to(target)
            protected=any(p.startswith('.') or p in ('data','uploads','outputs','node_modules','dist','__pycache__') for p in relative.parts)
            if file.is_file() and file.suffix in suffixes and not protected and not (source/relative).exists():file.unlink()
        for file in source.rglob('*'):
            if file.is_file():
                dest=target/file.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,dest)
    else:
        shutil.copyfile(source,target)
    sys.exit(0)
if name=='tar':
    if fail=='backup':sys.exit(2)
    import tarfile
    if args[0]=='-czf':
        with tarfile.open(args[1],'w:gz') as archive:archive.add(args[3],arcname='.')
    else:
        with tarfile.open(args[1],'r:gz') as archive:archive.getmembers()
    sys.exit(0)
if name=='docker':
    if args[:2]==['compose','version']:print('Docker Compose version v2');sys.exit(0)
    if args[:2]==['compose','config']:
        if fail=='config':sys.exit(1)
        if '--format' in args:print(json.dumps({'name':'office-agent','services':{'backend':{'volumes':[{'target':'/data','source':os.environ['DATA'],'type':'bind'}]}}}))
        sys.exit(0)
    if args[:3]==['compose','ps','-q']:print(args[-1]+'-id');sys.exit(0)
    if args[0]=='inspect':print(json.dumps([{'Destination':'/data','Source':os.environ['DATA'],'Type':'bind','RW':True}]));sys.exit(0)
    if args[0]=='exec':
        if "if 'permissions' == 'permissions'" in args[-1]:
            if fail=='permissions':print('Permission denied: /data/uploads',file=sys.stderr);sys.exit(1)
        else:
            if fail=='tasks' or fail=='late_tasks' and Path(os.environ['WEB_STOPPED']).exists():
                print('有文档任务正在执行',file=sys.stderr);sys.exit(1)
        sys.exit(0)
    if args[:2]==['compose','build']:
        if fail=='build_'+args[-1]:sys.exit(1)
        if fail=='slow_build':time.sleep(2)
        sys.exit(0)
    if args[0]=='stop':
        if args[1]=='web-id':Path(os.environ['WEB_STOPPED']).touch()
        sys.exit(0)
    if args[0]=='start':sys.exit(0)
    if args[:2]==['compose','up']:
        if fail=='startup':sys.exit(1)
        sys.exit(0)
    if args[:2]==['compose','exec']:
        if fail=='health':sys.exit(1)
        print('{"status":"ok"}');sys.exit(0)
    if args[:2]==['compose','port']:print('127.0.0.1:8080');sys.exit(0)
print('Unexpected mock command',name,args,file=sys.stderr);sys.exit(90)
'''


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='deploy-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'remote app'
        for name in ['backend/app', 'frontend/src']:
            (self.app/name).mkdir(parents=True)
        (self.app/'.env').write_text('KEEP_SECRET=yes')
        (self.app/'compose.yaml').write_text('name: office-agent\n')
        (self.app/'backend/app/obsolete.py').write_text('old')
        (self.app/'frontend/src/obsolete.ts').write_text('old')
        (self.app/'backend/app/.env').write_text('protected')
        (self.app/'backend/app/data').mkdir()
        (self.app/'backend/app/data/preserve.py').write_text('protected')
        self.data = self.root/'data';self.data.mkdir()
        (self.data/'uploads').mkdir()
        (self.data/'uploads/original.docx').write_bytes(b'unchanged')
        (self.data/'office_agent.db').write_bytes(b'unchanged database')
        self.backups=self.root/'backups'
        self.bin=self.root/'bin';self.bin.mkdir()
        for name in ['ssh','rsync','docker','tar']:
            path=self.bin/name;path.write_text(FAKE);path.chmod(0o755)
        self.log=self.root/'calls.jsonl'
        self.env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
                  'CALLS':str(self.log),'DATA':str(self.data),'WEB_STOPPED':str(self.root/'web-stopped')}
        self.command=[str(SCRIPTS/'deploy.sh'),'--host','test-alias','--remote-dir',str(self.app),'--backup-dir',str(self.backups)]

    def execute(self,*args,fail=''):
        return subprocess.run([*self.command,*args],env={**self.env,'FAIL':fail},text=True,capture_output=True,timeout=30)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_success_sync_protects_data_and_configuration(self):
        result=self.execute()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('部署成功',result.stdout)
        self.assertEqual((self.app/'.env').read_text(),'KEEP_SECRET=yes')
        self.assertEqual((self.app/'backend/app/.env').read_text(),'protected')
        self.assertTrue((self.app/'backend/app/data/preserve.py').exists())
        self.assertFalse((self.app/'backend/app/obsolete.py').exists())
        self.assertFalse((self.app/'frontend/src/obsolete.ts').exists())
        self.assertTrue((self.app/'backend/app/main.py').is_file())
        self.assertTrue((self.app/'frontend/src/App.vue').is_file())
        self.assertFalse((self.app/'backend/tests').exists())
        self.assertEqual((self.data/'uploads/original.docx').read_bytes(),b'unchanged')
        archives=list(self.backups.glob('*.tar.gz'));self.assertEqual(len(archives),1)
        import tarfile
        with tarfile.open(archives[0]) as archive:
            self.assertEqual(archive.extractfile('./office_agent.db').read(),b'unchanged database')
        calls=self.calls()
        self.assertLess(calls.index(['docker','compose','build','backend']),calls.index(['docker','compose','build','web']))
        self.assertTrue(any(c[:3]==['docker','compose','up'] for c in calls))

    def test_dry_run_has_no_server_writes_or_lifecycle_commands(self):
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.app.rglob('*') if p.is_file()}
        result=self.execute('--dry-run')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        after={str(p.relative_to(self.root)):p.read_bytes() for p in self.app.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        self.assertFalse((self.app.parent/('.'+self.app.name+'.deploy.lock')).exists())
        self.assertFalse(self.backups.exists())
        calls=self.calls()
        self.assertTrue(all('--dry-run' in c for c in calls if c[0]=='rsync'))
        self.assertFalse(any(c[0]=='tar' or c[:2] in [['docker','stop'],['docker','start']] or c[:3] in [['docker','compose','build'],['docker','compose','up']] for c in calls))

    def test_preflight_failures_prevent_sync(self):
        for failure in ['ssh','permissions','tasks','config']:
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                result=self.execute(fail=failure)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(any(c[0]=='rsync' for c in self.calls()))

    def test_sync_and_build_failures_never_stop_service(self):
        for failure in ['sync','build_backend','build_web']:
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                result=self.execute(fail=failure)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(any(c[:2]==['docker','stop'] for c in self.calls()))

    def test_backup_failure_restarts_original_containers(self):
        result=self.execute(fail='backup')
        self.assertNotEqual(result.returncode,0)
        self.assertIn(['docker','start','backend-id','web-id'],self.calls())
        self.assertFalse(any(c[:3]==['docker','compose','up'] for c in self.calls()))
        self.assertEqual(list(self.backups.glob('*.partial')),[])

    def test_new_task_after_ingress_stops_restores_service(self):
        result=self.execute(fail='late_tasks')
        self.assertNotEqual(result.returncode,0)
        self.assertIn(['docker','start','backend-id','web-id'],self.calls())
        self.assertNotIn(['docker','stop','backend-id'],self.calls())

    def test_startup_or_proxy_failure_never_claims_success(self):
        for failure in ['startup','health']:
            with self.subTest(failure=failure):
                result=self.execute(fail=failure)
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('部署成功',result.stdout)
                self.assertTrue(list(self.backups.glob('*.tar.gz')))
                self.assertIn('不要直接降级数据库',result.stdout)

    def test_concurrent_lock_rejects_second_deployment(self):
        import fcntl
        path=self.app.parent/('.'+self.app.name+'.deploy.lock')
        with path.open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.execute()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('已有部署',result.stderr)
        self.assertFalse(any(c[0]=='rsync' for c in self.calls()))

    def test_paths_and_symlink_rejected(self):
        for path in ['/','/srv','/srv/x/data','/srv/x/../app']:
            with self.subTest(path=path):
                result=self.execute('--remote-dir',path)
                self.assertNotEqual(result.returncode,0)
        (self.app/'backend/app/link.py').symlink_to(self.data/'office_agent.db')
        result=self.execute()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('符号链接',result.stderr)

    def test_remote_missing_dependency(self):
        (self.bin/'rsync').unlink()
        # A mock SSH makes the remote PATH contain only docker, tar and python.
        ssh=self.bin/'ssh'
        ssh.write_text('#!/usr/bin/env python3\nimport os,shlex,sys\nos.environ["PATH"]=os.path.dirname(sys.argv[0])\nos.execv("'+sys.executable+'",["python3","-c","import os,shlex,sys; os.execvp(sys.argv[1],sys.argv[1:])",*shlex.split(sys.argv[-1])])\n')
        (self.bin/'python3').symlink_to(sys.executable)
        # Local rsync check succeeds via a separate directory that the remote drops.
        localbin=self.root/'localbin';localbin.mkdir();(localbin/'rsync').write_text('#!/bin/sh\nexit 0\n');(localbin/'rsync').chmod(0o755)
        self.env['PATH']=str(self.bin)+os.pathsep+str(localbin)+os.pathsep+os.environ['PATH']
        result=self.execute()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('服务器缺少 rsync',result.stderr)

@unittest.skipUnless(shutil.which('rsync'), 'rsync unavailable')
class RealRsyncTests(unittest.TestCase):
    def test_real_filters_and_dry_run(self):
        import shlex
        from unittest.mock import patch
        actual_run = subprocess.run
        with tempfile.TemporaryDirectory(prefix='rsync-rules-') as directory:
            base=Path(directory);stage=base/'stage';stage.mkdir();target=base/'target with spaces';target.mkdir()
            deploy.stage_files(stage)
            for folder in ['backend/app','frontend/src']:(target/folder).mkdir(parents=True)
            (target/'backend/app/deleted.py').write_text('stale')
            (target/'frontend/src/deleted.vue').write_text('stale')
            (target/'backend/app/.env').write_text('secret')
            (target/'backend/app/state.db').write_text('database')
            (target/'backend/app/uploads').mkdir();(target/'backend/app/uploads/keep.py').write_text('uploaded')
            (target/'.env').write_text('server settings')
            def local_run(command,**kwargs):
                command=list(command);i=command.index('-e');del command[i:i+2]
                command[-1]=shlex.split(command[-1].split(':',1)[1])[0]
                return actual_run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kwargs)
            with patch.object(deploy.subprocess,'run',side_effect=local_run):
                deploy.sync(stage,'fake',str(target),True)
                self.assertTrue((target/'backend/app/deleted.py').exists())
                self.assertFalse((target/'backend/app/main.py').exists())
                deploy.sync(stage,'fake',str(target),False)
            self.assertFalse((target/'backend/app/deleted.py').exists())
            self.assertFalse((target/'frontend/src/deleted.vue').exists())
            self.assertEqual((target/'backend/app/.env').read_text(),'secret')
            self.assertEqual((target/'backend/app/state.db').read_text(),'database')
            self.assertTrue((target/'backend/app/uploads/keep.py').exists())
            self.assertEqual((target/'.env').read_text(),'server settings')
            self.assertTrue((target/'backend/app/main.py').exists())


if __name__ == '__main__':
    unittest.main()
