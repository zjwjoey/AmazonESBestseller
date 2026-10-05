# -*- coding: utf-8 -*-
'Ranking/detail collection and task-selection command handlers.'
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Callable

from .common import _load_json, _safe_print, _save_json

def cmd_collect(args, parser: argparse.ArgumentParser) -> None:
    '\u699c\u5355+\u8be6\u60c5\u4e32\u884c\u91c7\u96c6\uff1brankings.json/details.json \u7a33\u5b9a\u8f93\u51fa\u5230 out_dir \u6839\u3002'
    if args.offline:
        parser.error('collect \u9700\u8981\u8054\u7f51\uff0c\u4e0d\u80fd\u4e0e --offline \u540c\u7528')
    if not args.urls and not args.rankings_file:
        parser.error('collect \u9700\u8981 --urls \u6216 --rankings-file')
    from ..access.browser import BrowserSession
    from ..access.location import ensure_spain_delivery
    from ..collection.detail import (CURRENT_DETAIL_SCHEMA_VERSION,
                                    collect_details, reparse_saved_details)
    from ..collection.planning import DetailState, build_plan, collect_asins
    from ..collection.checkpoints import read_checkpoint
    from ..collection.ranking import collect_rankings

    out_dir = str(Path(args.out_dir).resolve())
    with BrowserSession(headless=not args.headful,
                        profile_dir=args.profile_dir or None) as session:
        session.challenge_wait_seconds = args.challenge_wait_seconds
        session.manual_assist = args.manual_assist
        location = ensure_spain_delivery(session, args.postal_code)
        if location is not None:
            _safe_print('\u914d\u9001\u5730\u70b9\u5df2\u786e\u8ba4\uff1a%s' % (location.text or '\u897f\u73ed\u7259'))
        if args.rankings_file:
            rankings = _load_json(args.rankings_file)
        elif args.pages_per_url != 1:
            rankings = collect_rankings(args.urls, session, out_dir,
                                        pages_per_url=args.pages_per_url)
        else:
            rankings = collect_rankings(args.urls, session, out_dir)
        # Quarantine affects detail planning only; raw ranking evidence remains
        # unchanged for audit/export.
        quarantine_dir = Path(out_dir) / 'quarantine'
        quarantined = {p.stem.upper() for p in quarantine_dir.rglob('*.html')}
        if args.rankings_only:
            _save_json(rankings, str(Path(out_dir) / 'rankings.json'))
            print('collect rankings-only \u5b8c\u6210\uff1a\u699c\u5355 %d \u6761 \u2192 %s' %
                  (len(rankings), Path(out_dir) / 'rankings.json'))
            return
        if args.manifest:
            manifest = _load_json(args.manifest)
            manifest_records = manifest.get('records', []) if isinstance(manifest, dict) else manifest
            allowed = {str(r.get('asin') or '').strip().upper() for r in manifest_records if isinstance(r, dict)}
            rankings = [r for r in rankings if str(r.get('asin') or '').strip().upper() in allowed]
            if not rankings:
                raise SystemExit('manifest \u4e0e\u699c\u5355\u8bb0\u5f55\u6ca1\u6709\u53ef\u5339\u914d\u7684 ASIN')
        planning_rankings = [r for r in rankings
                             if str(r.get('asin') or '').strip().upper() not in quarantined]
        skipped = len(rankings) - len(planning_rankings)
        if skipped:
            print('\u5df2\u8df3\u8fc7\u9694\u79bb ASIN %d \u6761\u8be6\u60c5\u8ba1\u5212\uff0c\u539f\u59cb\u699c\u5355\u8bc1\u636e\u4fdd\u7559' % skipped)
        state = DetailState(Path(out_dir) / 'state' / 'details_state.json')
        # Promote completed per-ASIN checkpoints before building the next plan;
        # this is what makes Ctrl-C/resume useful even when the batch summary
        # was never written.
        checkpoint_records = []
        for asin in {str(r.get('asin') or '').strip().upper() for r in rankings}:
            checkpoint = read_checkpoint(Path(out_dir) / 'checkpoints', asin)
            if checkpoint and checkpoint.get('status') == 'success' and checkpoint.get('record'):
                checkpoint_records.append(checkpoint['record'])
        if checkpoint_records:
            state.update(checkpoint_records)
            state.save()
        # Upgrade old cached records from local HTML before planning.  This is
        # deliberately offline and avoids re-requesting pages after a parser
        # schema bump.
        stale_asins = [r.get('asin') for r in state.records()
                       if int(r.get('detail_schema_version', 0) or 0)
                       < CURRENT_DETAIL_SCHEMA_VERSION]
        reparsed = (reparse_saved_details(Path(out_dir) / 'html', state,
                                           asins=stale_asins)
                    if stale_asins else [])
        if reparsed:
            state.save()
        plan = build_plan(planning_rankings, state)
        planned_asins = collect_asins(plan)
        def _collect_delta(*collect_args, **collect_kwargs):
            try:
                return collect_details(*collect_args, **collect_kwargs, write_summary=False)
            except TypeError as exc:
                if 'unexpected keyword argument' not in str(exc):
                    raise
                return collect_details(*collect_args, **collect_kwargs)
        if args.progress:
            def _write_progress(event):
                _save_json({'planned': len(planned_asins), **event}, args.progress)
            details = _collect_delta(planned_asins, session, out_dir,
                                     on_progress=_write_progress)
        else:
            details = _collect_delta(planned_asins, session, out_dir)
        state.update(details)
        state.save()
        # details.json \u7528 state \u5168\u91cf\u91cd\u5efa\uff1aresume \u573a\u666f\u4e0b collect_details \u53ea\u4ea7\u51fa
        # \u672c\u6b21\u589e\u91cf\uff08\u65b0\u589e/\u91cd\u91c7\uff09\uff0c\u76f4\u63a5\u8986\u76d6\u4f1a\u4e22\u5df2\u7f13\u5b58\u8be6\u60c5\uff1bstate \u662f\u8de8 run \u6743\u5a01
        # \u6301\u4e45\u7f13\u5b58\uff0c\u542b\u5168\u90e8 ASIN \u7684\u6700\u65b0\u8be6\u60c5\u3002
        _save_json(state.records(), str(Path(out_dir) / 'details.json'))

    # \u672c\u6b21\u91c7\u96c6\u7684\u699c\u5355\u4ea7\u7269\u590d\u5236\u5230 out_dir \u6839\uff0c\u4f9b enrich/qa/export \u8bfb\u53d6\u3002
    # details.json \u5df2\u7531 state \u5168\u91cf\u91cd\u5efa\uff0c\u7edd\u4e0d\u7528 run \u76ee\u5f55\u526f\u672c\u8986\u76d6\uff1b\u590d\u7528
    # --rankings-file \u65f6\u672c\u6b21\u6ca1\u6709\u65b0 run \u76ee\u5f55\uff0c\u8df3\u8fc7\u590d\u5236\uff0c\u907f\u514d\u65e7 run \u7684\u699c\u5355
    # \u8986\u76d6\u8c03\u7528\u65b9\u663e\u5f0f\u63d0\u4f9b\u7684\u8f93\u5165\uff08\u4f1a\u8ba9\u4e0b\u6e38 enrich \u4e0e\u672c\u6b21\u8be6\u60c5\u4e0d\u540c\u6e90\uff09\u3002
    if not args.rankings_file:
        runs = sorted(Path(out_dir).glob('runs/*'), reverse=True)
        if runs:
            src = runs[0] / 'rankings.json'
            if src.exists():
                shutil.copy(src, Path(out_dir) / 'rankings.json')
    print('collect \u5b8c\u6210\uff1a\u699c\u5355 %d \u6761\u3001\u8be6\u60c5 %d \u6761\u3001\u79bb\u7ebf\u91cd\u89e3\u6790 %d \u6761\u3001\u8ba1\u5212\u6536\u96c6 %d \u6761'
          % (len(rankings), len(details), len(reparsed), len(plan['collect'])))


def _batch_countdown(seconds: int, category_name: str) -> None:
    'Keep the process alive during inter-category cooldown with a live timer.'
    remaining = max(0, int(seconds))
    while remaining > 0:
        hours, rem = divmod(remaining, 3600)
        minutes, secs = divmod(rem, 60)
        print('\r[\u51b7\u5374\u5012\u8ba1\u65f6] %s\uff1a%02d:%02d:%02d' %
              (category_name, hours, minutes, secs), end='', flush=True)
        time.sleep(1)
        remaining -= 1
    if seconds > 0:
        print('\r[\u51b7\u5374\u5b8c\u6210] %s\uff1a\u5f00\u59cb\u4e0b\u4e00\u7c7b\u76ee                    ' % category_name,
              flush=True)


def _batch_countdown_until(deadline: float, category_name: str) -> None:
    'Resume an already persisted cooldown without resetting its deadline.'
    while True:
        remaining = max(0, int(deadline - time.time() + 0.999))
        if remaining <= 0:
            break
        hours, rem = divmod(remaining, 3600)
        minutes, secs = divmod(rem, 60)
        print('\r[\u51b7\u5374\u5012\u8ba1\u65f6] %s\uff1a%02d:%02d:%02d' %
              (category_name, hours, minutes, secs), end='', flush=True)
        time.sleep(min(1, remaining))
    print('\r[\u51b7\u5374\u5b8c\u6210] %s\uff1a\u5f00\u59cb\u4e0b\u4e00\u7c7b\u76ee                    ' % category_name,
          flush=True)


def _load_json_array_or_empty(path: Path) -> list:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SystemExit('\u6279\u5904\u7406\u8bc1\u636e\u6587\u4ef6\u635f\u574f\uff0c\u5df2\u505c\u6b62\uff08\u4e0d\u4f1a\u9759\u9ed8\u6e05\u7a7a\uff09: %s (%s)' %
                         (path, exc))
    if not isinstance(value, list):
        raise SystemExit('\u6279\u5904\u7406\u8bc1\u636e\u6587\u4ef6\u9876\u5c42\u5fc5\u987b\u662f\u6570\u7ec4: %s' % path)
    return value


def cmd_batch_collect(args, parser: argparse.ArgumentParser, *,
                      countdown: Callable[[int, str], None] = _batch_countdown,
                      countdown_until: Callable[[float, str], None] = _batch_countdown_until) -> None:
    'Run the corrected source plan category-by-category with resumable cooldown.'
    if args.offline:
        parser.error('batch-collect \u9700\u8981\u8054\u7f51\uff0c\u4e0d\u80fd\u4e0e --offline \u540c\u7528')
    plan_path = Path(args.plan)
    if not plan_path.exists():
        parser.error('\u627e\u4e0d\u5230\u63d0\u53d6\u8ba1\u5212: %s' % args.plan)
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    sources = plan.get('sources', []) if isinstance(plan, dict) else []
    if not isinstance(sources, list) or not sources:
        parser.error('\u63d0\u53d6\u8ba1\u5212\u6ca1\u6709 sources')
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / 'batch_state.json'
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise SystemExit('\u6279\u5904\u7406\u72b6\u6001\u635f\u574f\uff0c\u5df2\u505c\u6b62\uff08\u8bf7\u4eba\u5de5\u68c0\u67e5\u540e\u6062\u590d\uff09: %s (%s)' %
                             (state_path, exc))
        if not isinstance(state, dict):
            raise SystemExit('\u6279\u5904\u7406\u72b6\u6001\u9876\u5c42\u5fc5\u987b\u662f\u5bf9\u8c61: %s' % state_path)
    done_urls = set(str(u) for u in state.get('completed_source_urls', [])
                    if str(u).strip())
    done_categories = set(str(g) for g in state.get('completed_categories', [])
                          if str(g).strip())
    detail_asins = set(str(a).upper() for a in state.get('detail_asins', [])
                       if str(a).strip())
    pending_detail_asins = set(str(a).upper() for a in state.get('pending_detail_asins', [])
                               if str(a).strip())
    shortfall_sources = dict(state.get('shortfall_sources', {}) or {})
    all_rankings = _load_json_array_or_empty(out_dir / 'rankings.json')
    all_details = _load_json_array_or_empty(out_dir / 'details.json')
    # Seed the batch with validated records from the preceding 4,500-SKU run.
    for path_text in (getattr(args, 'seed_rankings', ''),):
        if path_text:
            for row in _load_json_array_or_empty(Path(path_text)):
                if not isinstance(row, dict):
                    continue
                try:
                    rank = int(row.get('bestseller_rank') or 0)
                except (TypeError, ValueError):
                    rank = 0
                if not 31 <= rank <= 50:
                    continue
                key = (row.get('ranking_source_url'), rank,
                       str(row.get('asin') or '').upper())
                if key not in {(r.get('ranking_source_url'), r.get('bestseller_rank'),
                                str(r.get('asin') or '').upper())
                               for r in all_rankings if isinstance(r, dict)}:
                    all_rankings.append(row)
    existing_products_path = getattr(args, 'existing_products', '')
    if existing_products_path:
        for row in _load_json_array_or_empty(Path(existing_products_path)):
            if isinstance(row, dict) and row.get('asin'):
                detail_asins.add(str(row['asin']).upper())
    existing_details_path = getattr(args, 'existing_details', '')
    if existing_details_path:
        for row in _load_json_array_or_empty(Path(existing_details_path)):
            if isinstance(row, dict) and row.get('asin'):
                all_details.append(row)
    detail_map = {str(r.get('asin') or '').upper(): r for r in all_details
                  if isinstance(r, dict) and r.get('asin')}
    detail_asins.update(detail_map)
    ranking_keys = {(r.get('ranking_source_url'), r.get('bestseller_rank'),
                     str(r.get('asin') or '').upper())
                    for r in all_rankings if isinstance(r, dict)}
    cooldown = (int(args.cooldown_seconds) if args.cooldown_seconds is not None
                else int(plan.get('cooldown_between_categories_seconds', 1800)))
    if cooldown < 0:
        parser.error('--cooldown-seconds \u4e0d\u80fd\u4e3a\u8d1f\u6570')

    # Plan status is evidence: completed/source_missing sources are not
    # requested again, even when the process was first started with empty state.
    for source in sources:
        if isinstance(source, dict) and source.get('status') in {'completed', 'source_missing'}:
            url = str(source.get('source_url') or '').strip()
            if url:
                done_urls.add(url)

    grouped = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        group = str(source.get('category_group') or 'unknown')
        grouped.setdefault(group, []).append(source)
    ordered_groups = sorted(grouped, key=lambda g: (
        min(int(s.get('category_sequence') or 999) for s in grouped[g]), g))
    pending_groups = []
    for group in ordered_groups:
        pending = [s for s in grouped[group]
                   if s.get('status') == 'pending' and
                   str(s.get('source_url') or '') not in done_urls]
        if pending:
            pending_groups.append((group, pending))
    if not pending_groups and (args.rankings_only or not pending_detail_asins):
        print('batch-collect\uff1a\u8ba1\u5212\u4e2d\u7684\u5f85\u63d0\u53d6\u6765\u6e90\u548c\u8be6\u60c5\u5df2\u5168\u90e8\u5b8c\u6210')
        return
    from ..access.browser import BrowserSession
    from ..access.location import ensure_spain_delivery
    from ..collection.detail import collect_details
    from ..collection.ranking import collect_rankings

    def save_state(active_group=None):
        state_path.write_text(json.dumps({
            'plan': str(plan_path),
            'completed_source_urls': sorted(done_urls),
            'completed_categories': sorted(done_categories),
            'detail_asins': sorted(detail_asins),
            'pending_detail_asins': sorted(pending_detail_asins),
            'shortfall_sources': shortfall_sources,
            'cooldown_until': state.get('cooldown_until'),
            'cooldown_category': state.get('cooldown_category'),
            'active_category': active_group,
            'completed_category_count': len(done_categories),
        }, ensure_ascii=False, indent=2), encoding='utf-8')

    with BrowserSession(headless=not args.headful,
                        profile_dir=args.profile_dir or None) as session:
        session.challenge_wait_seconds = args.challenge_wait_seconds
        session.manual_assist = args.manual_assist
        location = ensure_spain_delivery(session, args.postal_code)
        if location is not None:
            _safe_print('\u914d\u9001\u5730\u70b9\u5df2\u786e\u8ba4\uff1a%s' % (location.text or '\u897f\u73ed\u7259'))

        # A restart during the inter-category wait resumes the original
        # deadline; it never silently skips the configured cooling interval.
        persisted_until = state.get('cooldown_until')
        if cooldown == 0:
            # The final 4,500-SKU phase explicitly disabled inter-category
            # cooling. Any deadline left by an older phase is stale state and
            # must not block the next category (even if it is malformed).
            if persisted_until is not None or state.get('cooldown_category') is not None:
                print('[\u6279\u5904\u7406] \u5f53\u524d\u914d\u7f6e\u5df2\u53d6\u6d88\u7c7b\u76ee\u95f4\u51b7\u5374\uff0c\u5df2\u6e05\u9664\u5386\u53f2 cooldown \u72b6\u6001')
                state['cooldown_until'] = None
                state['cooldown_category'] = None
                save_state(None)
        elif persisted_until:
            try:
                deadline = float(persisted_until)
            except (TypeError, ValueError):
                raise SystemExit('\u6279\u5904\u7406\u51b7\u5374\u72b6\u6001\u65e0\u6548: cooldown_until')
            if deadline > time.time():
                countdown_until(deadline, str(state.get('cooldown_category') or '\u4e0a\u4e00\u7c7b\u76ee'))
            state['cooldown_until'] = None
            state['cooldown_category'] = None
            save_state(None)

        # Retry detail failures before moving on to a new category.  A failed
        # ASIN remains pending and therefore survives a process restart.
        if pending_detail_asins and not args.rankings_only:
            retry = sorted(pending_detail_asins - detail_asins)
            if retry:
                retry_details = collect_details(retry, session, str(out_dir / 'detail_cache'),
                                                write_summary=False)
                success = {str(r.get('asin') or '').upper() for r in retry_details if r.get('asin')}
                for record in retry_details:
                    asin = str(record.get('asin') or '').upper()
                    if asin:
                        detail_map[asin] = record
                        detail_asins.add(asin)
                pending_detail_asins.difference_update(success)
                all_details = list(detail_map.values())
                (out_dir / 'details.json').write_text(json.dumps(all_details, ensure_ascii=False, indent=2), encoding='utf-8')
                save_state(None)

        for group_index, (group, group_sources) in enumerate(pending_groups):
            category_name = str(group_sources[0].get('category_name_zh') or group)
            category_dir = out_dir / 'categories' / group
            category_dir.mkdir(parents=True, exist_ok=True)
            save_state(group)
            urls = [str(s['source_url']) for s in group_sources]
            print('\n[\u6279\u5904\u7406] %s\uff1a%d \u4e2a\u6765\u6e90\u9875\uff0c\u76ee\u6807\u6392\u540d31\u201350' %
                  (category_name, len(urls)))
            rankings = collect_rankings(urls, session, str(category_dir), pages_per_url=1)
            target = [r for r in rankings
                      if 31 <= int(r.get('bestseller_rank') or 0) <= 50]
            for r in target:
                r['batch_target_rank_range'] = '31-50'
                r['batch_category_group'] = group
                key = (r.get('ranking_source_url'), r.get('bestseller_rank'),
                       str(r.get('asin') or '').upper())
                if key not in ranking_keys:
                    all_rankings.append(r)
                    ranking_keys.add(key)
            (category_dir / 'rankings_31_50.json').write_text(
                json.dumps(target, ensure_ascii=False, indent=2), encoding='utf-8')
            # Validate each source independently.  A short page is retryable;
            # it must not be hidden by marking the whole category complete.
            complete_urls = set()
            for url in urls:
                count = sum(1 for r in target if str(r.get('ranking_source_url') or '') == url)
                if count == 20:
                    complete_urls.add(url)
                else:
                    shortfall_sources[url] = {'expected': 20, 'observed': count,
                                              'status': 'shortfall'}
            unique_targets = []
            seen_category = set()
            for r in target:
                asin = str(r.get('asin') or '').upper()
                if asin and asin not in seen_category:
                    seen_category.add(asin)
                    unique_targets.append(r)
            manifest = {'category_group': group, 'records': unique_targets,
                        'unique_asins': len(unique_targets), 'rank_range': [31, 50]}
            (category_dir / 'manifest_31_50.json').write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

            new_asins = [str(r['asin']).upper() for r in unique_targets
                         if str(r['asin']).upper() not in detail_asins]
            # Persist ranking evidence and the detail work queue before the
            # first detail request. An access stop during detail collection
            # therefore leaves a resumable checkpoint instead of losing the
            # just-collected source results.
            if not args.rankings_only:
                pending_detail_asins.update(new_asins)
            (out_dir / 'rankings.json').write_text(
                json.dumps(all_rankings, ensure_ascii=False, indent=2), encoding='utf-8')
            save_state(group)
            details = [] if args.rankings_only else (
                collect_details(new_asins, session, str(out_dir / 'detail_cache'),
                                write_summary=False)
                if new_asins else [])
            for record in details:
                asin = str(record.get('asin') or '').upper()
                if asin:
                    detail_map[asin] = record
                    detail_asins.add(asin)
            success_asins = {str(r.get('asin') or '').upper() for r in details if r.get('asin')}
            pending_detail_asins.difference_update(success_asins)
            all_details = list(detail_map.values())
            (out_dir / 'rankings.json').write_text(
                json.dumps(all_rankings, ensure_ascii=False, indent=2), encoding='utf-8')
            (out_dir / 'details.json').write_text(
                json.dumps(all_details, ensure_ascii=False, indent=2), encoding='utf-8')
            done_urls.update(complete_urls)
            if len(complete_urls) != len(urls):
                print('[\u6279\u5904\u7406] %s \u6765\u6e90\u77ed\u7f3a %d/%d\uff1b\u672a\u5b8c\u6210\u6765\u6e90\u4f1a\u5728\u4e0b\u6b21\u8fd0\u884c\u91cd\u8bd5' %
                      (category_name, len(urls) - len(complete_urls), len(urls)))
            done_categories.add(group)
            save_state(None)
            print('[\u6279\u5904\u7406] %s \u5b8c\u6210\uff1a31\u201350 \u699c\u5355 %d \u6761\uff0c\u65b0\u589e\u8be6\u60c5 %d \u6761' %
                  (category_name, len(target), len(details)))
            if group_index < len(pending_groups) - 1 and cooldown > 0:
                state['cooldown_until'] = time.time() + cooldown
                state['cooldown_category'] = category_name
                save_state(group)
                countdown_until(float(state['cooldown_until']), category_name)
                state['cooldown_until'] = None
                state['cooldown_category'] = None
                save_state(None)
    if not pending_groups and not pending_detail_asins:
        print('batch-collect\uff1a\u8ba1\u5212\u4e2d\u7684\u5f85\u63d0\u53d6\u6765\u6e90\u548c\u8be6\u60c5\u5df2\u5168\u90e8\u5b8c\u6210')
    print('batch-collect \u5b8c\u6210\uff1a\u699c\u5355 %d \u6761\uff0c\u8be6\u60c5 %d \u6761\uff1b\u72b6\u6001\u6587\u4ef6 %s' %
          (len(all_rankings), len(all_details), state_path))


def cmd_select_quota(args) -> None:
    '\u6839\u636e\u5df2\u91c7\u96c6\u699c\u5355\u548c\u5ba1\u6838\u8fc7\u7684 URL \u914d\u7f6e\u751f\u6210 150/50 manifest\u3002'
    from ..collection.quota import annotate_groups, normalize_group, select_quota, validate_category_config

    rankings = _load_json(args.rankings)
    config = _load_json(args.config)
    try:
        rows = validate_category_config(config)
    except ValueError as exc:
        raise SystemExit('%s: %s' % (args.config, exc))
    quotas: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        group = normalize_group(row.get('category_group') or row.get('group'))
        if not group:
            raise SystemExit('\u7c7b\u76ee\u914d\u7f6e\u7f3a\u5c11 group: %r' % row)
        try:
            quota = int(row.get('quota'))
        except (TypeError, ValueError):
            raise SystemExit('\u7c7b\u76ee\u914d\u7f6e quota \u5fc5\u987b\u662f\u6574\u6570: %r' % row)
        quotas[group] = quotas.get(group, 0) + quota
    tagged = annotate_groups(rankings, rows)
    try:
        selected = select_quota(tagged, quotas)
    except ValueError as exc:
        raise SystemExit(str(exc))
    records = [item for group in quotas for item in selected[group]]
    summary = {group: len(selected[group]) for group in quotas}
    summary['total'] = len(records)
    _save_json({'summary': summary, 'records': records}, args.out)
    print('select-quota \u5b8c\u6210\uff1a\u5bb6\u5c45 %d\u3001DIY %d\u3001\u603b\u8ba1 %d \u2192 %s'
          % (summary.get('hogar', 0), summary.get('diy', 0), len(records), args.out))


def cmd_download_images(args) -> None:
    '\u6309 ASIN \u4e0b\u8f7d\u7f3a\u5931\u539f\u56fe\uff1b\u4e32\u884c\u3001\u53ef\u6062\u590d\uff0c\u4e0d\u8c03\u7528 DS\u3002'
    from ..collection.images import download_images
    records = _load_json(args.products)
    if not isinstance(records, list):
        raise SystemExit('products JSON \u9876\u5c42\u5fc5\u987b\u662f\u6570\u7ec4: %s' % args.products)
    result = download_images(records, args.out_dir, delay_seconds=args.delay)
    _save_json(result, args.report)
    print('download-images \u5b8c\u6210\uff1a\u4e0b\u8f7d %d\u3001\u7f13\u5b58 %d\u3001\u5931\u8d25 %d \u2192 %s' %
          (sum(v.get('status') == 'downloaded' for v in result.values()),
           sum(v.get('status') == 'cached' for v in result.values()),
          sum(v.get('status') == 'failed' for v in result.values()), args.report))


def cmd_reconcile_task(args) -> None:
    from ..qa.reconcile import reconcile_task
    task = _load_json(args.task)
    items = _load_json(args.items)
    products = _load_json(args.products)
    translations = _load_json(args.translations) if args.translations else []
    report = reconcile_task(task, items, products, translations=translations)
    _save_json(report, args.out)
    print('reconcile-task\uff1a%s\uff0c\u76ee\u6807 %d \u2192 %s' %
          (report['status'], report['target_count'], args.out))
