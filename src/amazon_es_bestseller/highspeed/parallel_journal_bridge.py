"""TEST_ONLY coordinator for ordering queue and immutable receipt writes.

This is an offline fault-injection prototype, not a live runner. It purposely
cannot start a browser or label responses as live capture.
"""
import hashlib
from amazon_es_bestseller.access.detector import detect_access_status


class OfflineJournalBridge:
    def __init__(self, queue, store, plan, sources, *, on_stop=None, on_access_receipt=None):
        if plan.get('origin')!='OFFLINE_FIXTURE' or plan.get('live_enabled') is not False:
            raise ValueError('TEST_ONLY_OFFLINE_PLAN_REQUIRED')
        self.queue=queue;self.store=store;self.plan=plan;self.sources=sources
        self.on_stop=on_stop or (lambda:None)
        self.on_access_receipt=on_access_receipt or (lambda state,receipt:None)

    def stop(self, reason):
        # Close the in-memory dispatch gate before even the queue write: a
        # slow or failed disk must never release another queued request.
        try:self.on_stop()
        finally:self.queue.stop(reason)

    def reserve(self, worker, *, now, observed_at):
        url=self.queue.claim(worker,now=now)
        if url is None:return None
        try:
            source=self.sources[url]
            key=source['source_id'] if url==source.get('actual_source_url') else (
                source['source_id']+'_'+hashlib.sha256(url.encode()).hexdigest()[:12])
            request={'kind':'PAGE','url':url,'source_id':source['source_id'],
                'source_type':source['source_type'],'observed_at':observed_at,
                'limits':self.plan,'page_key':key,'origin':'OFFLINE_FIXTURE'}
            reservation=self.store.reserve_request(self.plan['run_id'],request)
            return {'worker':worker,'url':url,'source':source,'page_key':key,'reservation':reservation}
        except BaseException:
            # Do not release the claim: the write outcome may be ambiguous.
            self.stop('JOURNAL_FAILURE')
            raise

    def save_response(self, item, response):
        state=detect_access_status(response.get('status_code'),response.get('text','')).value
        data={**response,'request_url':item['url'],'kind':'PAGE','page_key':item['page_key'],
              'source_id':item['source']['source_id'],'source_type':item['source']['source_type'],
              'origin':'OFFLINE_FIXTURE','access_state':state}
        try:
            if state!='NORMAL':
                # Close the claim gate before any potentially slow disk writes.
                self.stop('ACCESS_STOP')
            saved=self.store.save_response(self.plan['run_id'],item['reservation'],data)
            if state!='NORMAL':
                self.on_access_receipt(state,saved)
                self.queue.complete(item['worker'],item['url'],state='ACCESS_STOP',receipt=saved['sha256'])
            else:
                self.queue.complete(item['worker'],item['url'],state='SAVED',receipt=saved['sha256'])
            return saved
        except BaseException:
            self.stop('JOURNAL_FAILURE')
            raise
