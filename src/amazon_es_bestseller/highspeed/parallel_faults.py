"""Durable fault evidence and desktop notices; acknowledgement never resumes LIVE."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import traceback
from uuid import uuid4


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        staging.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def desktop_notice(path):
    """The popup runs separately so it cannot block the evidence writer."""
    if sys.platform != 'win32':
        print('SNAPSHOT_ALERT: ' + str(path), file=sys.stderr, flush=True)
        return 'console'
    subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--popup', str(path)],
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    return 'desktop_process_started'


class FaultReporter:
    def __init__(self, root, run_id, plan_sha256, *, notify=desktop_notice, fallback_root=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',run_id):raise ValueError('SAFE_RUN_ID_REQUIRED')
        self.root = Path(root)
        self.run_id = run_id
        self.plan_sha256 = plan_sha256
        self.notify = notify
        self.fallback_root = Path(fallback_root or (
            Path('F:/ai文件/CodexStorage/work/AmazonESBestseller/alerts') if sys.platform == 'win32'
            else self.root.parent / 'snapshot-alert-fallback'))

    def record(self, *, worker, url, request_id, stage, error, scope):
        value = {'kind': 'SNAPSHOT_FAULT_V1', 'incident_id': uuid4().hex,
                 'observed_at': datetime.now(timezone.utc).isoformat(), 'run_id': self.run_id,
                 'plan_sha256': self.plan_sha256, 'worker': worker, 'url': url,
                 'request_id': request_id, 'stage': stage, 'scope': scope,
                 'exception_type': type(error).__name__, 'message': str(error),
                 'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__)),
                 'automatic_retry': False, 'acknowledgement_resumes_collection': False}
        path = self.root / 'alerts' / (value['incident_id'] + '.json')
        try:
            _write(path, value)
        except OSError as persistence_error:
            value['primary_persistence_error'] = str(persistence_error)
            path = self.fallback_root / self.run_id / (value['incident_id'] + '.json')
            try:
                _write(path, value)
            except OSError as fallback_error:
                value.update(persisted=False, persistence_error=str(fallback_error))
                print('SNAPSHOT_FAULT_PERSISTENCE_FAILED: ' + json.dumps(value, ensure_ascii=True),
                      file=sys.stderr, flush=True)
                return value
        result = {**value, 'path': str(path.resolve()), 'persisted': True}
        try:
            result['notification'] = self.notify(path)
        except Exception as notice_error:
            result['notification_error'] = str(notice_error)
            print('SNAPSHOT_NOTICE_FAILED: ' + str(path), file=sys.stderr, flush=True)
        # A failed desktop launch must remain visible after process exit.
        try:
            _write(path.with_suffix('.notification.json'), {
                k: result[k] for k in ('incident_id', 'notification', 'notification_error') if k in result})
        except OSError:
            pass
        return result


async def wait_for_operator(reporter, reason, *, interval=1):
    """Keep the browser owner alive, with no requests or automatic gate release."""
    waiting = {'kind': 'SNAPSHOT_OPERATOR_WAIT_V1', 'wait_id': uuid4().hex,
               'run_id': reporter.run_id, 'plan_sha256': reporter.plan_sha256,
               'reason': reason, 'started_at': datetime.now(timezone.utc).isoformat(),
               'status': 'WAITING_FOR_OPERATOR', 'live_resumed': False}
    directory = reporter.root / 'alerts'
    try:
        _write(directory / 'waiting.json', waiting)
    except OSError:
        directory = reporter.fallback_root / reporter.run_id / 'alerts'
        try:_write(directory / 'waiting.json', waiting)
        except OSError:
            print('SNAPSHOT_WAIT_STORAGE_UNAVAILABLE: collection remains paused; Ctrl+C exits.',
                  file=sys.stderr,flush=True)
    print('SNAPSHOT_WAITING_FOR_OPERATOR: ' + str(directory / 'waiting.json'), flush=True)
    try:
        while True:
            control = directory / ('exit_' + waiting['wait_id'] + '.json')
            if control.is_file():
                try:value = json.loads(control.read_text('utf-8'))
                except (ValueError,OSError):value={}
                if not isinstance(value,dict):value={}
                if all(value.get(k) == waiting[k] for k in ('wait_id', 'run_id', 'plan_sha256')) and value.get('action') == 'EXIT_WAIT':
                    return {'action': 'EXIT_WAIT', 'live_resumed': False}
            await asyncio.sleep(interval)
    finally:
        waiting.update(status='WAIT_ENDED', live_resumed=False)
        try:
            _write(directory / 'waiting.json', waiting)
        except OSError:
            pass


def acknowledge_wait(root, run_id, *, exit_wait=False):
    directory = Path(root) / 'alerts'
    waiting = json.loads((directory / 'waiting.json').read_text('utf-8'))
    if waiting.get('run_id') != run_id or waiting.get('status') != 'WAITING_FOR_OPERATOR':
        raise ValueError('EXACT_ACTIVE_WAIT_REQUIRED')
    value = {k: waiting[k] for k in ('wait_id', 'run_id', 'plan_sha256')}
    value.update(action='EXIT_WAIT' if exit_wait else 'ACKNOWLEDGE', live_resumed=False)
    _write(directory / (('exit_' if exit_wait else 'ack_') + waiting['wait_id'] + '.json'), value)
    return value


def _popup(path):
    import ctypes
    value = json.loads(Path(path).read_text('utf-8'))
    scope = ('窗口 ' + str(value['worker']) + ' 已停用，其他正常窗口可继续。'
             if value['scope'] == 'worker' else '所有采集已暂停，程序保留等待处理。')
    text = ('快照任务出现异常\n\n' + scope + '\n'
            '任务：' + value['run_id'] + '\n'
            '原因：' + value['exception_type'] + '：' + value['message'][:500] + '\n\n'
            '已有数据和断点保留，不会自动重试未确认请求。\n'
            '关闭本提醒不会恢复采集。\n\n错误记录：' + str(path))
    # OK + warning + foreground + topmost. No resume action exists in this UI.
    ctypes.windll.user32.MessageBoxW(None, text, 'Amazon 快照异常提醒', 0x30 | 0x10000 | 0x40000)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--popup')
    parser.add_argument('--root')
    parser.add_argument('--run-id')
    parser.add_argument('--exit-wait', action='store_true')
    args = parser.parse_args(argv)
    if args.popup:
        if sys.platform != 'win32':
            raise ValueError('WINDOWS_DESKTOP_REQUIRED')
        _popup(args.popup)
    else:
        if not args.root or not args.run_id:
            parser.error('--root and --run-id are required')
        result = acknowledge_wait(args.root, args.run_id, exit_wait=args.exit_wait)
        print(json.dumps(result, ensure_ascii=False))
        return result


if __name__ == '__main__':
    main()
