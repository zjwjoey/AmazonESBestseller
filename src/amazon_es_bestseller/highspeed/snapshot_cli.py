"""Unified snapshot tools. Offline commands never instantiate a browser."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import uuid

from ..multisource.storage import immutable_json, digest
from .cli import load_json, reference
from .global_target import SNAPSHOT_PROFILES
from .store import ReadOnlyRunStore


def task_config(profile='main-priority-50000'):
    if profile not in SNAPSHOT_PROFILES:
        raise ValueError('UNKNOWN_SNAPSHOT_TARGET_PROFILE')
    policy = deepcopy(SNAPSHOT_PROFILES[profile])
    # Finite planning envelope, not a promise of reachable supply or execution approval.
    pages = {'BEST_SELLERS': 2200, 'NEW_RELEASES': 2200, 'CUSTOMER_FAVORITES': 600} if policy['target'] == 50000 else {
        'BEST_SELLERS': 550, 'NEW_RELEASES': 450, 'CUSTOMER_FAVORITES': 200}
    return {'config_contract': 'CURRENT_PAGE_TASK_CONFIG_V1', 'target_policy': policy,
            'source_budgets': {k: {'requests': n, 'PAGE': n, 'native': 0} for k, n in pages.items()},
            'snapshot_field_contract': 'CARD_IMAGE_V1', 'max_duration_hours': 24,
            'delivery_preflight_contract': 'SPAIN_UI_PREFLIGHT_V1',
            'request_pacing_seconds': 3, 'category_cooldown_seconds': 1800, 'parallelism': 1,
            'max_raw_observations_per_run': 500000, 'history_observation_limit': 1000000,
            'approved': False, 'live_enabled': False}


def validate_config(config):
    from .global_target import is_global_policy
    if config.get('config_contract') != 'CURRENT_PAGE_TASK_CONFIG_V1':
        raise ValueError('SNAPSHOT_CONFIG_CONTRACT_REQUIRED')
    policy = config.get('target_policy')
    if not is_global_policy(policy) or policy not in SNAPSHOT_PROFILES.values():
        raise ValueError('SNAPSHOT_PROFILE_REQUIRED')
    if config.get('approved') is not False or config.get('live_enabled') is not False:
        raise ValueError('CONFIG_IS_NOT_EXECUTION_APPROVAL')
    for key, lower, upper in (('request_pacing_seconds', 3, 3600), ('category_cooldown_seconds', 1800, 86400),
                              ('max_duration_hours', 1, 24), ('parallelism', 1, 1),
                              ('max_raw_observations_per_run', 1, 1000000), ('history_observation_limit', 1, 1000000)):
        if type(config.get(key)) is not int or not lower <= config[key] <= upper:
            raise ValueError('BOUNDED_SNAPSHOT_CONFIG_REQUIRED:' + key)
    budgets = config.get('source_budgets', {})
    if set(budgets) != set(policy['source_minima']):
        raise ValueError('ALL_THREE_SOURCE_BUDGETS_REQUIRED')
    for values in budgets.values():
        if (set(values) != {'requests', 'PAGE', 'native'} or values['native'] != 0
                or any(type(v) is not int or v < 0 for v in values.values())
                or not 1 <= values['PAGE'] == values['requests'] <= 6000):
            raise ValueError('FINITE_PAGE_ONLY_BUDGET_REQUIRED')
    if sum(v['requests'] for v in budgets.values()) > 6000:
        raise ValueError('SNAPSHOT_REQUEST_ENVELOPE_EXCEEDED')
    if config.get('snapshot_field_contract') != 'CARD_IMAGE_V1':
        raise ValueError('SNAPSHOT_IMAGE_FIELD_CONTRACT_REQUIRED')
    if config.get('delivery_preflight_contract') != 'SPAIN_UI_PREFLIGHT_V1':
        raise ValueError('SNAPSHOT_DELIVERY_PREFLIGHT_CONTRACT_REQUIRED')


def prepare_page_task(base_plan, base_approval, config, output, runtime_root, task_path):
    from .v4_authorization import validate_plan_v4
    from .page_snapshot import validate_page_snapshot
    from .budgets import utc_text
    validate_config(config); validate_page_snapshot(base_plan)
    if base_approval.get('plan_sha256') != digest(base_plan):
        raise ValueError('SNAPSHOT_BASE_AUTH_PLAN_MISMATCH')
    run_id = 'snapshot' + str(config['target_policy']['target']) + '_' + uuid.uuid4().hex[:12]
    plan = deepcopy(base_plan)
    for key in ('parent_run_id', 'seed_reference', 'continuation_mode'):
        plan.pop(key, None)
    plan.update(run_id=run_id, snapshot_id=run_id, campaign_id=run_id, campaign_created_at=utc_text(),
        task_reference=reference(task_path), target_policy=deepcopy(config['target_policy']),
        source_targets=deepcopy(config['target_policy']['source_minima']), target_unique_links=config['target_policy']['target'],
        task_clock_path=str((Path(runtime_root) / run_id / 'task_clock.json').resolve()), live_enabled=False)
    for key in ('source_budgets', 'max_duration_hours', 'request_pacing_seconds', 'category_cooldown_seconds', 'parallelism',
                'max_raw_observations_per_run', 'history_observation_limit', 'snapshot_field_contract', 'delivery_preflight_contract'):
        plan[key] = deepcopy(config[key])
    cap = sum(v['requests'] for v in plan['source_budgets'].values())
    plan.update(max_requests_per_run=cap, max_requests_per_day=cap, max_pages_per_run=cap)
    # Do not carry old-run page expansion/amendment proofs into a new batch.
    if any(s.get('snapshot_admission_root') or s.get('favorite_budget_reference') for s in plan['sources']):
        raise ValueError('FRESH_REVIEWED_ROOT_SOURCES_REQUIRED')
    validate_plan_v4(plan)
    approval = deepcopy(base_approval)
    for key in ('favorite_budget_reference', 'time_extension_reference', 'response_alias_reference',
                'network_recovery_reference', 'agent_recovery_reference'):
        approval.pop(key, None)
    for key in ('run_id', 'snapshot_id', 'campaign_id', 'campaign_created_at', 'task_reference', 'target_policy',
                'source_targets', 'target_unique_links', 'source_budgets', 'max_duration_hours', 'request_pacing_seconds',
                'category_cooldown_seconds', 'parallelism', 'max_raw_observations_per_run', 'history_observation_limit',
                'snapshot_field_contract', 'delivery_preflight_contract', 'max_requests_per_run', 'max_requests_per_day', 'max_pages_per_run'):
        approval[key] = deepcopy(plan[key])
    approval.update(approved=False, approved_by=None, approval_id=None, reviewed_at=None, expires_at=None,
        plan_sha256=digest(plan), task_clock_reference=None, store_root=str((Path(runtime_root)/run_id/'store').resolve()))
    directory = Path(output).resolve() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    immutable_json(directory/'plan.pending.json', plan)
    immutable_json(directory/'authorization.pending.json', approval)
    immutable_json(directory/'config.json', config)
    return {'directory': str(directory), 'run_id': run_id, 'approved': False, 'live_started': False,
            'target': plan['target_unique_links'], 'source_minima': plan['source_targets'],
            'source_spread_and_supply': 'NOT_PROVEN', 'task_clock_started': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    p = sub.add_parser('profile', help='Write a pending task configuration only.')
    p.add_argument('--target-profile', choices=tuple(SNAPSHOT_PROFILES), default='main-priority-50000')
    p.add_argument('--out', required=True)
    p = sub.add_parser('prepare-page', help='Offline new plan proposal from reviewed PAGE roots; no approval.')
    for opt in ('base-plan', 'base-authorization', 'config', 'out', 'runtime-root', 'task'):
        p.add_argument('--' + opt, required=True)
    for name in ('status', 'preflight', 'pause', 'benchmark'):
        p = sub.add_parser(name); p.add_argument('--root', required=True); p.add_argument('--run-id', required=True)
        if name == 'preflight': p.add_argument('--authorization', required=True)
        if name == 'benchmark': p.add_argument('--out', required=True)
    args, rest = parser.parse_known_args(argv)
    if rest: parser.error('unrecognized arguments: ' + ' '.join(rest))
    if args.operation == 'profile':
        result = task_config(args.target_profile); immutable_json(Path(args.out), result)
    elif args.operation == 'prepare-page':
        result = prepare_page_task(load_json(args.base_plan), load_json(args.base_authorization), load_json(args.config),
                                   args.out, args.runtime_root, args.task)
    else:
        store = ReadOnlyRunStore(args.root)
        try:
            from .snapshot_report import inspect_snapshot
            result = inspect_snapshot(store, args.run_id)
            plan = store.get_run(args.run_id)['plan']
            if args.operation == 'preflight':
                from .v4_authorization import validate_v4_authorization
                from .runner import HighSpeedRunner
                issues = []
                try:
                    validate_v4_authorization(plan, load_json(args.authorization), HighSpeedRunner(store)._gate(plan), store_root=store.root)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    issues.append(str(exc))
                if any(r['status'] == 'RESERVED' for r in store.requests(args.run_id)):
                    issues.append('AMBIGUOUS_UNFINISHED_REQUESTS_NO_AUTO_RETRY')
                result.update(preflight='BLOCKED' if issues else 'OFFLINE_CHECKS_PASSED', issues=issues,
                              current_delivery='REQUIRES_LIVE_UI_VERIFICATION', browser_started=False)
            if args.operation == 'pause':
                from .snapshot_control import request_pause
                result = request_pause(store.root, plan)
            if args.operation == 'benchmark':
                from .snapshot_benchmark import benchmark_saved_snapshot
                result = benchmark_saved_snapshot(store, args.run_id, args.out)
        finally:
            store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def dispatch(args):
    argv = args.snapshot_args
    live_operations = {'run', 'resume', 'configure-delivery'}
    if argv and argv[0] in live_operations and getattr(args, 'offline', False):
        raise ValueError('OFFLINE_SNAPSHOT_LIVE_FORBIDDEN')
    local = {'profile', 'prepare-page', 'status', 'preflight', 'pause', 'benchmark'}
    if argv and argv[0] in local:
        return main(argv)
    from .v4_cli import main as v4_main
    return v4_main(argv)


def register(sub):
    p = sub.add_parser('snapshot', help='PAGE快照：配置、只读审查、暂停恢复；执行必须显式--live',
        description='PAGE快照统一入口：profile / prepare-page / status / preflight / pause / report / benchmark / run / resume。',
        epilog='各子命令参数见：amazon-es snapshot <子命令> --help。配置、审查不会启动浏览器；--offline 禁止运行采集。')
    p.add_argument('snapshot_args', nargs=argparse.REMAINDER)
    p.set_defaults(func=dispatch)
