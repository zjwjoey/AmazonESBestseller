"""Apply separately saved exact tracking-alias evidence, never fuzzy URL matching."""
import hashlib,json
from pathlib import Path
from ..multisource.storage import digest


def apply_alias_reviews(queue,plan,manifest):
    if manifest.get('run_id')!=plan['run_id'] or manifest.get('plan_sha256')!=digest(plan):
        raise ValueError('ALIAS_MANIFEST_PLAN_BINDING_REQUIRED')
    results=[]
    for ref in manifest.get('reviews',[]):
        path=Path(ref['path']);body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=ref['sha256']:raise ValueError('ALIAS_REVIEW_HASH_CHANGED')
        review=json.loads(body)
        if review.get('kind')!='TRACKING_PATH_ALIAS_REVIEW_V1':raise ValueError('REAL_ALIAS_REVIEW_REQUIRED')
        urls=review['urls']
        states={r[0]:r[3] for r in queue.snapshot()['pages']}
        if all(states.get(u) in {'ALIASED','COMMITTED','PENDING'} for u in urls) and any(states.get(u)=='ALIASED' for u in urls):
            # Reopen is read-only only when every alias retains this exact proof.
            aliases={r[0]:r for r in queue.snapshot()['aliases']}
            if any(aliases[u][2:]!=(str(path.resolve()),ref['sha256']) for u in urls if states.get(u)=='ALIASED'):
                raise ValueError('EXISTING_ALIAS_PROOF_MISMATCH')
            continue
        results.append(queue.resolve_tracking_path_aliases(urls,ref))
    return results
