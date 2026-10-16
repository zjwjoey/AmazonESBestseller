"""Offline scheduling scenarios only. This module cannot authorize or run LIVE.

Source chains stay on one lane. All lanes share one serial browser dispatcher,
one >=3s start interval and a finite request envelope. Local work overlap is a
hypothesis, not a measured parallel collector or evidence of safe native replay.
"""
from collections import deque
import math


FAMILIES={'BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES'}


def replay_trace(jobs, *, lanes=1, pacing_seconds=3, serial_local_fraction=0,
                 request_cap=None, source_order=None):
    if type(lanes) is not int or lanes not in (1,3):
        raise ValueError('ONLY_BOUNDED_ONE_OR_THREE_LANE_EVALUATION')
    if (type(pacing_seconds) not in (int,float) or not math.isfinite(pacing_seconds)
            or pacing_seconds<3):
        raise ValueError('GLOBAL_PACING_MUST_REMAIN_AT_LEAST_THREE_SECONDS')
    if (type(serial_local_fraction) not in (int,float)
            or not math.isfinite(serial_local_fraction) or not 0<=serial_local_fraction<=1):
        raise ValueError('FINITE_SHARED_LOCAL_FRACTION_REQUIRED')
    if request_cap is None:request_cap=len(jobs)
    if type(request_cap) is not int or request_cap<0:raise ValueError('FINITE_REQUEST_CAP_REQUIRED')
    order=list(source_order or dict.fromkeys(j['source_id'] for j in jobs))
    if len(set(order))!=len(order):raise ValueError('UNIQUE_FROZEN_SOURCE_ORDER_REQUIRED')
    assignment={sid:i%lanes for i,sid in enumerate(order)}
    queues=[deque() for _ in range(lanes)]
    ids=set()
    for job in jobs:
        if (job['source_id'] not in assignment or job.get('source_type') not in FAMILIES
                or job.get('kind') not in {'PAGE','ACP'}):raise ValueError('EXACT_ORIGINAL_SOURCE_AND_KIND_REQUIRED')
        if job.get('access_state')!='NORMAL':raise ValueError('UNSAFE_TRACE_CANNOT_BE_MODELED_AS_HEALTHY')
        if job['request_id'] in ids:raise ValueError('DUPLICATE_REQUEST_ID')
        ids.add(job['request_id'])
        for field in ('network_seconds','local_seconds'):
            if type(job.get(field)) not in (int,float) or not math.isfinite(job[field]) or job[field]<0:
                raise ValueError('FINITE_NONNEGATIVE_MEASURED_DURATION_REQUIRED')
        queues[assignment[job['source_id']]].append(job)
    ready=[0.0]*lanes;browser_free=0.0;next_permit=0.0;schedule=[]
    while any(queues) and len(schedule)<request_cap:
        lane=min((i for i in range(lanes) if queues[i]),key=lambda i:(ready[i],i))
        job=queues[lane].popleft()
        start=max(ready[lane],browser_free,next_permit)
        network_end=start+job['network_seconds']
        browser_free=network_end+job['local_seconds']*serial_local_fraction
        ready[lane]=browser_free+job['local_seconds']*(1-serial_local_fraction)
        next_permit=start+pacing_seconds
        schedule.append({'request_id':job['request_id'],'source_id':job['source_id'],
            'lane':lane,'start':start,'network_end':network_end,'work_end':ready[lane]})
    return {'offline_only':True,'executable':False,'network_actions':0,
        'lanes':lanes,'requests':len(schedule),'elapsed_seconds':max(ready,default=0),
        'serial_local_fraction_assumption':serial_local_fraction,'schedule':schedule,
        'limitations':['Not a live executor or parallel correctness test.',
            'Recorded local work may include unmeasured serial browser work, locks and IPC.',
            'No claim that browser native protocols, shared budget or Gate are parallel-ready.',
            'Final independent DOM/receipt/deduplication QA is not shortened by this model.']}
