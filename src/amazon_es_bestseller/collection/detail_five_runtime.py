"""Explicit, approval-bound V2 five-lane entry; never a default mode switch."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess

from ..multisource.storage import digest,file_lock,immutable_json,sealed
from .detail_parallel_authorization import validate_approval,SharedDetailGates,instant
from .detail_parallel_browser import ParallelBrowserCapture
from .detail_parallel_runtime import ParallelDetailRunner
from .parallel_detail_store import ParallelDetailStore


def current_code():
    root=Path(__file__).resolve().parents[3]
    sha=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True)
    if dirty.strip():raise ValueError('CLEAN_REVIEWED_FIVE_LANE_CODE_REQUIRED')
    return sha


async def run(plan_path,approval_path,root,profile,*,validate_only=False):
    plan=json.loads(Path(plan_path).read_text(encoding='utf8'))
    approval_bytes=Path(approval_path).read_bytes()
    approval=json.loads(approval_bytes)
    sha=current_code()
    budget=validate_approval(approval,plan,root,profile,sha)
    gates=SharedDetailGates(approval['access_gate_paths'])
    gates.require_normal()
    if validate_only:
        return {'state':'VALIDATED_NOT_STARTED','parser_contract':2,'lanes':5,
                'plan_sha256':digest(plan),'code_sha':sha,'budget':budget}
    root=Path(root).resolve();profile=Path(profile).resolve()
    def check():
        if digest(Path(approval_path).read_bytes())!=digest(approval_bytes):
            raise ValueError('LIVE_APPROVAL_CHANGED')
        if datetime.now(timezone.utc)>=min(instant(approval['deadline']),instant(approval['request_deadline'])):
            raise ValueError('ORIGINAL_REQUEST_DEADLINE_REACHED')
        gates.require_normal()
    with file_lock(root/'parallel_profile_owner.lock'):
        # Preflight history before opening a browser. No success is refetched,
        # and unresolved old captures cannot be silently promoted or retried.
        store=ParallelDetailStore(root,plan,budget)
        store.import_legacy_successes();store.pending()
        immutable_json(root/'parallel_execution_approval.json',sealed(approval))
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            context=await playwright.chromium.launch_persistent_context(str(profile),headless=False)
            capture=ParallelBrowserCapture(context,check_gate=check)
            runner=None
            try:
                await capture.open()
                runner=ParallelDetailRunner(root,plan,budget,capture=capture,
                    check_gate=check,stop_gate=gates.stop,
                    interval=approval['request_start_interval_seconds'])
                result=await runner.run()
                if result['state']=='ACCESS_STOP':
                    await hold_visible(context,approval)
                return result
            except Exception:
                if runner is not None:runner.scheduler.stop.set()
                # Route guards reject all new document navigation while saved
                # pages remain available for inspection. Never auto-resume.
                if runner is not None:await hold_visible(context,approval)
                raise
            finally:await context.close()


async def hold_visible(context,approval):
    deadline=instant(approval['deadline'])
    while datetime.now(timezone.utc)<deadline and any(not p.is_closed() for p in context.pages):
        await asyncio.sleep(1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',required=True)
    parser.add_argument('--approval',required=True)
    parser.add_argument('--out-dir',required=True)
    parser.add_argument('--profile-dir',required=True)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    print(json.dumps(asyncio.run(run(args.plan,args.approval,args.out_dir,args.profile_dir,
                                    validate_only=args.validate_only)),ensure_ascii=True))


if __name__=='__main__':main()
