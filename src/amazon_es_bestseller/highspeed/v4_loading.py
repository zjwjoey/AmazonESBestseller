"""Metered real browser loading; no synthesized endpoints or request bodies."""
from pathlib import Path
import hashlib
import json
from urllib.parse import urlsplit,parse_qs

from ..transport.playwright import PlaywrightTransport, _CONTROLLED_ACP_NONCE_HEADER
from ..multisource.storage import read_sealed
from .cli import save_native_contract
from .v4_sources import amazon_url

PATHS={'DEALS':'/d2b/api/v1/products/search','AMAZON_HAUL':'/acp/'}


def dynamic_endpoint(url,kind):
    path=urlsplit(url).path
    return (path==PATHS['DEALS'] if kind=='DEALS' else
        path.startswith('/acp/') and '/haul-cx-infinite-scroll-body/' in path
        and path.endswith('/getLazyLoadedAsins'))


def validate_dynamic_descriptor(url, source, scope, authorization, descriptor):
    d=descriptor or {}
    if (scope.get('allow_native_dynamic') is not True or source['source_type'] not in PATHS
            or not amazon_url(url) or not dynamic_endpoint(url,source['source_type'])
            or d.get('url')!=url or d.get('source_id')!=source['source_id']
            or d.get('run_id')!=authorization['run_id']
            or d.get('parent_page_url')!=source['actual_source_url']
            or d.get('method')!=('GET' if source['source_type']=='DEALS' else 'POST')
            or d.get('request_contract_source')!='NATIVE_BROWSER_REQUEST_HELD'):
        raise ValueError('V4_DYNAMIC_EXACT_NATIVE_SOURCE_REQUIRED')
    path=Path(d['native_contract_evidence_path']); raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=d['native_contract_evidence_sha256']:
        raise ValueError('V4_DYNAMIC_NATIVE_SHA_MISMATCH')
    native=json.loads(raw)
    for k in ('url','method','payload','headers','parent_page_url','source_id','run_id',
              'request_contract_source','source_type','parent_html_sha256','parent_html_path'):
        if native.get(k)!=d.get(k):
            raise ValueError('V4_DYNAMIC_NATIVE_BYTES_OR_BINDING_MISMATCH')
    if (native.get('request_sent') is not False or native.get('resource_type') not in {'xhr','fetch'}
            or hashlib.sha256(Path(d['parent_html_path']).read_bytes()).hexdigest()!=d['parent_html_sha256']):
        raise ValueError('V4_DYNAMIC_PARENT_DOM_REQUIRED')
    # Context and genuine cards must be independently established in this run.
    from .v4_adapters import parse_v4_page
    parsed=parse_v4_page(Path(d['parent_html_path']).read_text(encoding='utf8'),source=source,
        page_url=source['actual_source_url'],run_id=d['run_id'],observed_at=d['captured_at'],
        evidence_path=d['parent_html_path'],origin='OFFLINE_FIXTURE')
    if parsed['page']['status']!='VERIFIED_WITH_PRODUCTS':
        raise ValueError('V4_DYNAMIC_PARENT_PRODUCTS_UNVERIFIED')
    if source['source_type']=='DEALS':
        q=parse_qs(urlsplit(url).query)
        context=json.loads((q.get('rankingContext') or ['{}'])[0])
        filters=json.loads((q.get('filters') or ['{}'])[0])
        if (context.get('pageTypeId')!='deals' or not (q.get('startIndex') or [''])[0].isdigit()
                or source['source_subtype']=='LIGHTNING_DEAL_FILTER' and
                'LIGHTNING_DEAL' not in filters.get('promotionTypes',[])):
            raise ValueError('V4_DEALS_NATIVE_FILTER_CONTEXT_REQUIRED')
    if source['source_type']=='AMAZON_HAUL':
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(Path(d['parent_html_path']).read_bytes(),'lxml')
        owners=[n for n in soup.select('[data-acp-path][data-acp-params]')
            if urlsplit(url).path==n['data-acp-path'].rstrip('/')+'/getLazyLoadedAsins'
            and n['data-acp-params']==d['headers'].get('x-amz-acp-params')]
        body=json.loads(d['payload'])
        if (len(owners)!=1 or not isinstance(body.get('widgetId'),str)
                or body['widgetId'] not in str(owners[0])
                or not isinstance(body.get('impressedAsins'),list)
                or not body['impressedAsins']
                or any(not isinstance(asin,str) or asin not in str(owners[0]) for asin in body['impressedAsins'])):
            raise ValueError('V4_HAUL_SAVED_OWNER_AND_NATIVE_ITEMS_REQUIRED')


class V4LoadingTransport(PlaywrightTransport):
    def __init__(self,session,*,store,plan,**kwargs):
        super().__init__(session,**kwargs)
        self.store=store;self.plan=plan;self.dynamic=[];self.dynamic_pending={}
        self._native_dynamic_paths=set(PATHS.values())
        self.loading_events=[]

    def install_dynamic_capture(self):
        for kind,path in PATHS.items():
            def capture(route,kind=kind):
                req=route.request
                headers={k.lower():v for k,v in req.headers.items()}
                key=(req.url,req.method.upper(),req.post_data or '',headers.get(_CONTROLLED_ACP_NONCE_HEADER))
                if key==self._manual_acp_allowance and key[-1]:
                    self._manual_acp_allowance=None
                    wire=req.all_headers()
                    route.continue_(headers={k:v for k,v in wire.items() if k.lower()!=_CONTROLLED_ACP_NONCE_HEADER})
                    return
                source=next((s for s in self.plan['sources'] if s['source_type']==kind
                    and s['actual_source_url']==self.session.page.url),None)
                if (not source or not amazon_url(req.url) or req.resource_type not in {'xhr','fetch'}
                        or len(self.dynamic)>=32):
                    route.abort();return
                selected={k:v for k,v in headers.items() if k in {'accept','content-type','referer'} or k.startswith('x-amz-')}
                self.dynamic.append({'route':route,'value':{'url':req.url,'method':req.method.upper(),
                    'payload':req.post_data or '', 'headers':selected,'parent_page_url':self.session.page.url,
                    'source_id':source['source_id'],'source_type':kind,'resource_type':req.resource_type,
                    'request_contract_source':'NATIVE_BROWSER_REQUEST_HELD','request_sent':False}})
            pattern='**'+path+'*' if kind=='DEALS' else '**/acp/**/haul-cx-infinite-scroll-body/**/getLazyLoadedAsins*'
            self.session.page.route(pattern,capture)

    def prepare_dynamic(self,source,receipt):
        page=self.session.page
        for attempt in range(4):
            available=[x for x in self.dynamic if x['value']['parent_page_url']==page.url and not x.get('prepared')]
            if available:
                item=available[0];item['prepared']=True
                d=item['value'];d.update(parent_html_path=receipt['html_path'],parent_html_sha256=receipt['sha256'])
                save_native_contract(self.store,self.plan,d)
                d['captured_at']=json.loads(Path(d['native_contract_evidence_path']).read_text(encoding='utf8'))['captured_at']
                return {**d,'referer':source['actual_source_url']}
            # Actual visible UI only; no application factories or invented API.
            if source['source_type']=='DEALS':
                # SeeMoreButton in the actual saved page expands departments
                # or brands. Product continuation is a grid scroll sentinel.
                page.evaluate("""() => { const g=document.querySelector('[data-testid="discount-asin-grid"]');
                    const c=g && g.querySelectorAll('[data-testid="product-card"]');
                    if(c && c.length)c[c.length-1].scrollIntoView({block:'end',behavior:'instant'});
                    window.scrollBy(0,Math.max(160,window.innerHeight)); }""")
            else:
                page.evaluate('() => window.scrollTo(0,document.body.scrollHeight)')
            page.wait_for_timeout(500)
        return None

    def fetch_ajax(self,url,**kwargs):
        response=super().fetch_ajax(url,**kwargs)
        if any(dynamic_endpoint(url,kind) for kind in PATHS):
            self.dynamic_pending[(url,kwargs['method'],kwargs.get('payload') or '')]=response
        return response

    def acknowledge_native_response(self,receipt):
        key=(receipt.get('request_url'),receipt.get('request_method'),receipt.get('request_payload') or '')
        response=self.dynamic_pending.pop(key,None)
        if response is None:return super().acknowledge_native_response(receipt)
        if (receipt.get('status_code')!=200 or receipt.get('access_state')!='NORMAL'
                or receipt.get('final_url')!=key[0] or response.url!=key[0]):return False
        proof=read_sealed(receipt['evidence_path'])
        if proof.get('sha256')!=hashlib.sha256(response.text.encode('utf8')).hexdigest():return False
        d=receipt['native_acp_descriptor'];source=next(s for s in self.plan['sources'] if s['source_id']==d['source_id'])
        # Revalidate against the immutable descriptor before local delivery.
        validate_dynamic_descriptor(key[0],source,{'allow_native_dynamic':True},self.plan,d)
        item=next((x for x in self.dynamic if x.get('prepared') and not x.get('delivered')
            and (x['value']['url'],x['value']['method'],x['value']['payload'])==key),None)
        if item is None or self.session.page.url!=d['parent_page_url']:return False
        body=Path(proof['html_path']).read_text(encoding='utf8')
        if body!=response.text:return False
        content_type=next((v for k,v in response.headers.items() if k.lower()=='content-type'),'application/json')
        item['route'].fulfill(status=200,body=body,headers={'content-type':content_type});item['delivered']=True
        self.loading_events.append({'origin':'LOCAL_UI_REPLAY','new_network_request':False,
            'request_id':receipt['request_id'],'request_url':key[0],
            'receipt_reference':{'path':receipt['evidence_path'],'sha256':hashlib.sha256(Path(receipt['evidence_path']).read_bytes()).hexdigest()}})
        return True

    def cancel_dynamic(self):
        for item in self.dynamic:
            if not item.get('delivered'):
                try:item['route'].abort()
                except Exception:pass
        self.dynamic.clear()
