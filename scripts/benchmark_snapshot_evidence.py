"""Read saved snapshot metadata/metrics only; never opens a browser or writable DB."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from amazon_es_bestseller.highspeed.store import ReadOnlyRunStore
from amazon_es_bestseller.highspeed.three_metrics import measure_three
from amazon_es_bestseller.highspeed.v4_quality import build_candidates_v4
from amazon_es_bestseller.multisource.storage import immutable_json


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True); p.add_argument('--run-id', required=True); p.add_argument('--out', required=True)
    args = p.parse_args(argv)
    root = Path(args.root).resolve(); output = Path(args.out).resolve()
    if output == root or output.is_relative_to(root): raise ValueError('OUTPUT_MUST_BE_OUTSIDE_EVIDENCE')
    before = {str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
              for path in root.rglob('*') if path.is_file()}
    t = time.perf_counter(); store = ReadOnlyRunStore(root)
    try:
        run = store.get_run(args.run_id); rows = list(store.iter_observations(args.run_id))
        loaded = time.perf_counter()
        metrics = measure_three(rows, args.run_id, target_policy=run['plan']['target_policy'])
        measured = time.perf_counter()
        candidates = build_candidates_v4(rows)
        aggregated = time.perf_counter()
        from amazon_es_bestseller.highspeed.snapshot_metrics import SnapshotMetricsIndex
        index = SnapshotMetricsIndex(args.run_id, run['plan']['target_policy'])
        samples = []
        for offset in range(0, len(rows), 50):
            tick = time.perf_counter()
            index.add(rows[offset:offset+50]); index.measure()
            samples.append(time.perf_counter()-tick)
        incremental_finished = time.perf_counter()
        parity = index.measure() == metrics
    finally: store.close()
    after = {str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
             for path in root.rglob('*') if path.is_file()}
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    result = {'run_id': args.run_id, 'observations': len(rows), 'candidate_rows': len(candidates),
        'metrics': metrics, 'seconds': {'readonly_database_and_projection': loaded-t,
            'full_metrics': measured-loaded, 'candidate_aggregation': aggregated-measured,
            'incremental_all_batches': incremental_finished-aggregated,
            'incremental_average_50_observation_update': sum(samples)/len(samples) if samples else 0},
        'incremental_full_metric_exact_parity': parity,
        'existing_file_count': len(before), 'changed_existing_or_new_paths': changed,
        'production_acceptance': 'NOT_EVALUATED', 'new_network_requests': 0, 'translation_API_calls': 0,
        'input_observation_origins': sorted({r.get('origin') for r in rows}),
        'input_observations_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    immutable_json(output, result)
    print(json.dumps({k:v for k,v in result.items() if k != 'metrics'}, ensure_ascii=False, indent=2))
    if changed: raise ValueError('EVIDENCE_METADATA_CHANGED_DURING_READ_ONLY_BENCHMARK')
    if not parity: raise ValueError('INCREMENTAL_METRICS_MISMATCH')


if __name__ == '__main__': main()
