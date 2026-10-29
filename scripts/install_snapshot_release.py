"""Install a tested side-by-side release without replacing local source edits or task state."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def files(root):
    return {p.relative_to(root).as_posix():sha(p) for directory in ('src','scripts')
            for p in (root/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}


def protected_state(task):
    if task is None:return {}
    result={}
    names=('plan.approved.json','authorization.approved.json','task_clock.json','code_manifest.json',
           'store/index.sqlite3','store/index.sqlite3-wal','store/index.sqlite3-shm','store/parallel_queue.sqlite',
           'store/parallel_queue.sqlite-wal','store/parallel_queue.sqlite-shm','store/access_gate.json',
           'store/snapshot_progress.json','store/snapshot_pause.json','store/snapshot_pause_release.json')
    for name in names:
        p=task/name
        if not p.is_file():continue
        stat=p.stat();result[name]={'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,
                                  'sha256':sha(p) if stat.st_size<1024*1024 else None}
    return result


def assert_no_collector():
    if sys.platform!='win32':return
    command="Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -notmatch 'pytest' -and ($_.CommandLine -match 'parallel_cli|start_runner.py|start_verified_parallel.py' -or ($_.CommandLine -match '(snapshot|task-collect)' -and $_.CommandLine -match '--live')) } | Select-Object -ExpandProperty ProcessId | ConvertTo-Json -Compress"
    result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
    if result.returncode or result.stdout.strip():raise RuntimeError('COLLECTOR_ACTIVE_OR_PROCESS_CHECK_UNAVAILABLE')


def install(source, runtime, release, *, protected_task=None, apply=False):
    source=Path(source).resolve();runtime=Path(runtime).resolve();release=Path(release).resolve()
    if release.exists():raise ValueError('NEW_RELEASE_DIRECTORY_REQUIRED')
    if sys.platform=='win32' and not release.is_relative_to(Path('F:/ai文件').resolve()):
        raise ValueError('RELEASE_MUST_BE_UNDER_F_AI_WORKSPACE')
    if source==runtime or release.is_relative_to(source) or release.is_relative_to(runtime):
        raise ValueError('ISOLATED_RELEASE_DIRECTORY_REQUIRED')
    initial=files(runtime);protected=protected_state(protected_task)
    assert_no_collector()
    if not apply:return {'ready':True,'live_started':False,'runtime_written':False,'protected_files':len(protected)}
    release.mkdir(parents=True)
    shutil.copytree(source/'src',release/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (release/'scripts').mkdir()
    shutil.copyfile(source/'scripts/amazon_es.py',release/'scripts/amazon_es.py')
    (release/'docs').mkdir()
    shutil.copyfile(source/'docs/SNAPSHOT_FAULT_ALERTS.md',release/'docs/SNAPSHOT_FAULT_ALERTS.md')
    (release/'code-manifest.json').write_text(json.dumps(files(release),indent=2),encoding='utf-8')
    # Snapshot all source edits, including untracked modules, before routing
    # imports away from the old package. No data or browser profiles are copied.
    previous=release/'previous-local-code';previous.mkdir()
    for relative,expected in initial.items():
        p=runtime/relative;target=previous/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target)
        if sha(target)!=expected:raise RuntimeError('LOCAL_CODE_CHANGED_DURING_BACKUP')
    if files(runtime)!=initial or protected_state(protected_task)!=protected:
        raise RuntimeError('SOURCE_OR_PROTECTED_STATE_CHANGED_DURING_PREPARATION')
    targets=['src/amazon_es_bestseller/__init__.py','运行程序.ps1',
             'scripts/Invoke-HighSpeed.ps1','scripts/Invoke-Multisource.ps1']
    backups={}
    for relative in targets:
        p=runtime/relative
        if not p.resolve().is_relative_to(runtime):raise ValueError('RUNTIME_TARGET_OUTSIDE_WORKSPACE')
        if p.is_file():
            backups[relative]=p.read_bytes();target=previous/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(backups[relative])
        else:backups[relative]=None
    init=release/'src/amazon_es_bestseller/__init__.py'
    proxy=('"""Active release selector; previous local code is preserved in the release backup."""\n'
           'import importlib.util as _util\nimport sys as _sys\n'
           f'_init = {str(init)!r}\n'
           f'_spec = _util.spec_from_file_location(__name__, _init, submodule_search_locations=[{str(init.parent)!r}])\n'
           '_module = _util.module_from_spec(_spec)\n_sys.modules[__name__] = _module\n_spec.loader.exec_module(_module)\n')
    helper=str(release/'scripts/amazon_es.py').replace("'","''")
    launcher=("$ErrorActionPreference = 'Stop'\n"
              f"$runtimePython = '{sys.executable.replace(chr(39),chr(39)*2)}'\n"
              f"$runtimeRunner = '{helper}'\n"
              "$runtimeArguments = @($args)\nif ($runtimeArguments.Count -eq 0) { $runtimeArguments = @('--help') }\n"
              "Push-Location -LiteralPath $PSScriptRoot\ntry {\n    & $runtimePython -B $runtimeRunner @runtimeArguments\n"
              "    $runtimeExitCode = $LASTEXITCODE\n} finally { Pop-Location }\nexit $runtimeExitCode\n")
    def replace(relative,body):
        staging=release/(uuid4().hex+'.stage')
        staging.write_bytes(body);os.replace(staging,runtime/relative)
    assert_no_collector()
    try:
        replace(targets[0],proxy.encode('utf-8'));replace(targets[1],launcher.encode('utf-8-sig'))
        for relative in targets[2:]:
            (runtime/relative).unlink(missing_ok=True)
        result=subprocess.run([sys.executable,'-B',str(release/'scripts/amazon_es.py'),'--runtime-info'],
                              cwd=runtime,capture_output=True,text=True,timeout=30)
        if result.returncode:raise RuntimeError('RELEASE_IMPORT_SMOKE_FAILED')
        info=json.loads(result.stdout)
        if Path(info['project_root']).resolve()!=release:raise RuntimeError('RELEASE_IMPORT_PATH_MISMATCH')
        # Also test the old checkout import path: it must select the new package.
        code=f'import sys,json;sys.path.insert(0,{str(runtime/"src")!r});import amazon_es_bestseller;print(json.dumps(amazon_es_bestseller.__file__))'
        selected=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=30)
        if selected.returncode or Path(json.loads(selected.stdout)).resolve()!=init:
            raise RuntimeError('OLD_IMPORT_PATH_STILL_SELECTS_OLD_CODE')
        after=files(runtime)
        excluded=set(targets)
        if {k:v for k,v in initial.items() if k not in excluded}!={k:v for k,v in after.items() if k not in excluded}:
            raise RuntimeError('UNRELATED_LOCAL_CODE_CHANGED')
        if protected_state(protected_task)!=protected:raise RuntimeError('PROTECTED_TASK_STATE_CHANGED')
    except BaseException:
        for relative,body in backups.items():
            if body is None:(runtime/relative).unlink(missing_ok=True)
            else:replace(relative,body)
        raise
    receipt={'release':str(release),'runtime':str(runtime),'live_started':False,'hot_update':False,
             'new_default_import':str(init),'old_live_launchers_removed':targets[2:],
             'original_local_code_backup':str(previous),'unrelated_local_code_preserved':True,
             'protected_task_state_preserved':True,'protected_state':protected,
             'code_manifest_sha256':sha(release/'code-manifest.json')}
    (release/'install-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--runtime',required=True);parser.add_argument('--release',required=True)
    parser.add_argument('--protected-task');parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(install(args.source,args.runtime,args.release,
        protected_task=Path(args.protected_task) if args.protected_task else None,apply=args.apply),ensure_ascii=True))


if __name__=='__main__':main()
