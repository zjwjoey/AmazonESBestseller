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
        and path.rsplit('/',1)[-1] in {'getAsins','getLazyLoadedAsins'})


def same_loading_view(view,root):
    if view==root:return True
    a,b=urlsplit(view),urlsplit(root)
    if not amazon_url(view) or (a.scheme,a.netloc,a.path)!=(b.scheme,b.netloc,b.path) or a.fragment:return False
    if b.path!='/deals':return False
    q=parse_qs(a.query,keep_blank_values=True);base=parse_qs(b.query,keep_blank_values=True)
    if any(len(v)!=1 for v in q.values()) or any(q.get(k)!=v for k,v in base.items()):return False
    extra=set(q)-set(base)
    allowed={'promotionsSearchLastSeenAsin','promotionsSearchStartIndex','promotionsSearchPageSize'}
    if not extra or not extra.issubset(allowed):return False
    import re
    for k in extra:
        if k.endswith('Asin'):
            if not re.fullmatch('[A-Z0-9]{10}',q[k][0]):return False
        elif not q[k][0].isdigit():return False
    return True


def validate_dynamic_descriptor(url, source, scope, authorization, descriptor):
    d=descriptor or {}
    if (scope.get('allow_native_dynamic') is not True or source['source_type'] not in PATHS
            or not amazon_url(url) or not dynamic_endpoint(url,source['source_type'])
            or d.get('url')!=url or d.get('source_id')!=source['source_id']
            or d.get('run_id')!=authorization['run_id']
            or d.get('root_page_url',d.get('parent_page_url'))!=source['actual_source_url']
            or not same_loading_view(d.get('parent_page_url',''),source['actual_source_url'])
            or d.get('method')!=('GET' if source['source_type']=='DEALS' else 'POST')
            or d.get('request_contract_source')!='NATIVE_BROWSER_REQUEST_HELD'):
        raise ValueError('V4_DYNAMIC_EXACT_NATIVE_SOURCE_REQUIRED')
    path=Path(d['native_contract_evidence_path']); raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=d['native_contract_evidence_sha256']:
        raise ValueError('V4_DYNAMIC_NATIVE_SHA_MISMATCH')
    native=json.loads(raw)
    for k in ('url','method','payload','headers','parent_page_url','source_id','run_id',
              'request_contract_source','source_type','parent_html_sha256','parent_html_path','root_page_url',
              'owner_document_reference','root_capture_receipt_reference','preceding_dynamic_receipt_reference'):
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
        parent_soup=BeautifulSoup(Path(d['parent_html_path']).read_bytes(),'lxml')
        owner_ref=d.get('owner_document_reference')
        if owner_ref:
            root_ref=d['root_capture_receipt_reference'];root_path=Path(root_ref['path'])
            if hashlib.sha256(root_path.read_bytes()).hexdigest()!=root_ref['sha256']:
                raise ValueError('V4_HAUL_ORIGINAL_DOCUMENT_RECEIPT_SHA_REQUIRED')
            root=read_sealed(root_path)
            if (root.get('run_id')!=d['run_id'] or root.get('source_id')!=source['source_id']
                    or root.get('request_url')!=source['actual_source_url'] or root.get('kind')!='PAGE'
                    or root.get('status_code')!=200 or root.get('access_state')!='NORMAL'
                    or owner_ref!={'path':root.get('document_html_path'),'sha256':root.get('document_sha256')}):
                raise ValueError('V4_HAUL_ORIGINAL_OWNED_DOCUMENT_REQUIRED')
            owner_bytes=Path(owner_ref['path']).read_bytes()
            if hashlib.sha256(owner_bytes).hexdigest()!=owner_ref['sha256']:
                raise ValueError('V4_HAUL_OWNER_DOCUMENT_SHA_MISMATCH')
            soup=BeautifulSoup(owner_bytes,'lxml')
        else:
            soup=parent_soup
        owners=[n for n in soup.select('[data-acp-path][data-acp-params]')
            if urlsplit(url).path==n['data-acp-path'].rstrip('/')+'/'+urlsplit(url).path.rsplit('/',1)[-1]
            and n['data-acp-params']==d['headers'].get('x-amz-acp-params')]
        body=json.loads(d['payload'])
        values=body.get('asins') if urlsplit(url).path.endswith('/getAsins') else body.get('impressedAsins')
        if (len(owners)!=1 or not isinstance(body.get('widgetId'),str)
                or body['widgetId'] not in str(owners[0])
                or not isinstance(values,list) or not values or not owners[0].get('id')):
            raise ValueError('V4_HAUL_SAVED_OWNER_AND_NATIVE_ITEMS_REQUIRED')
        current=parent_soup.find_all(id=owners[0]['id'])
        if len(current)!=1 or current[0].get('data-card-metrics-id')!=owners[0].get('data-card-metrics-id'):
            raise ValueError('V4_HAUL_CURRENT_DOM_OWNER_REQUIRED')
        proof_text=str(owners[0])+str(current[0])
        preceding=d.get('preceding_dynamic_receipt_reference')
        if preceding:
            prev_path=Path(preceding['path'])
            if hashlib.sha256(prev_path.read_bytes()).hexdigest()!=preceding['sha256']:
                raise ValueError('V4_HAUL_PRECEDING_RECEIPT_SHA_REQUIRED')
            prev=read_sealed(prev_path);prev_native=prev.get('native_acp_descriptor') or {}
            if (prev.get('run_id')!=d['run_id'] or prev.get('source_id')!=source['source_id']
                    or prev.get('status_code')!=200 or prev.get('access_state')!='NORMAL'
                    or prev.get('origin')!='LIVE_CAPTURE' or prev.get('kind')!='ACP'
                    or prev.get('request_method')!='POST' or prev.get('request_payload')!=prev_native.get('payload')
                    or prev.get('final_url')!=prev.get('request_url')
                    or urlsplit(prev.get('request_url','')).path!=owners[0]['data-acp-path'].rstrip('/')+'/getLazyLoadedAsins'
                    or prev_native.get('headers',{}).get('x-amz-acp-params')!=owners[0]['data-acp-params']
                    or json.loads(prev_native.get('payload','{}')).get('widgetId')!=body['widgetId']
                    or prev_native.get('root_capture_receipt_reference')!=d.get('root_capture_receipt_reference')):
                raise ValueError('V4_HAUL_PRECEDING_OWNED_RECOMMENDATION_REQUIRED')
            prev_bytes=Path(prev['html_path']).read_bytes()
            if hashlib.sha256(prev_bytes).hexdigest()!=prev['sha256']:
                raise ValueError('V4_HAUL_PRECEDING_RESPONSE_SHA_REQUIRED')
            if urlsplit(url).path.endswith('/getAsins'):proof_text+=prev_bytes.decode('utf8')
        if any(not isinstance(asin,str) or asin not in proof_text for asin in values):
            raise ValueError('V4_HAUL_NATIVE_ITEMS_HAVE_NO_OWNED_EVIDENCE')


class V4LoadingTransport(PlaywrightTransport):
    def __init__(self,session,*,store,plan,**kwargs):
        super().__init__(session,**kwargs)
        self.store=store;self.plan=plan;self.dynamic=[];self.dynamic_pending={}
        self._native_dynamic_paths=set(PATHS.values())
        self.loading_events=[]
        self.current_document_url=None
        self._root_loading_receipt=None
        self._last_dynamic_receipt=None

    def fetch_page(self,url,**kwargs):
        self.current_document_url=url
        self._root_loading_receipt=None
        self._last_dynamic_receipt=None
        return super().fetch_page(url,**kwargs)

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
                    and s['actual_source_url']==self.current_document_url
                    and same_loading_view(self.session.page.url,s['actual_source_url'])),None)
                if (not source or not amazon_url(req.url) or req.resource_type not in {'xhr','fetch'}
                        or len(self.dynamic)>=32):
                    route.abort();return
                selected={k:v for k,v in headers.items() if k in {'accept','content-type','referer'} or k.startswith('x-amz-')}
                self.dynamic.append({'route':route,'value':{'url':req.url,'method':req.method.upper(),
                    'payload':req.post_data or '', 'headers':selected,'parent_page_url':self.session.page.url,
                    'source_id':source['source_id'],'source_type':kind,'resource_type':req.resource_type,
                    'root_page_url':source['actual_source_url'],
                    'request_contract_source':'NATIVE_BROWSER_REQUEST_HELD','request_sent':False}})
            pattern='**'+path+'*' if kind=='DEALS' else '**/acp/**/haul-cx-infinite-scroll-body/**/get*Asins*'
            self.session.page.route(pattern,capture)

    def prepare_dynamic(self,source,receipt):
        page=self.session.page
        if self._root_loading_receipt is None:self._root_loading_receipt=receipt
        for attempt in range(4):
            available=[x for x in self.dynamic if x['value']['root_page_url']==source['actual_source_url'] and not x.get('prepared')]
            if available:
                item=available[0];item['prepared']=True
                d=item['value'];d.update(parent_html_path=receipt['html_path'],parent_html_sha256=receipt['sha256'])
                if source['source_type']=='AMAZON_HAUL':
                    root=self._root_loading_receipt
                    d.update(owner_document_reference={'path':root['document_html_path'],'sha256':root['document_sha256']},
                        root_capture_receipt_reference={'path':root['evidence_path'],'sha256':hashlib.sha256(Path(root['evidence_path']).read_bytes()).hexdigest()})
                    if self._last_dynamic_receipt and self._last_dynamic_receipt['request_url'].split('?',1)[0].endswith('/getLazyLoadedAsins'):
                        prev=self._last_dynamic_receipt
                        d['preceding_dynamic_receipt_reference']={'path':prev['evidence_path'],'sha256':hashlib.sha256(Path(prev['evidence_path']).read_bytes()).hexdigest()}
                save_native_contract(self.store,self.plan,d)
                d['captured_at']=json.loads(Path(d['native_contract_evidence_path']).read_text(encoding='utf8'))['captured_at']
                return {**d,'referer':source['actual_source_url']}
            # Actual visible UI only; no application factories or invented API.
            if source['source_type']=='DEALS':
                # SeeMoreButton in the actual saved page expands departments
                # or brands. Product continuation is a grid scroll sentinel.
                page.evaluate("""() => { const g=document.querySelector('[data-testid="discount-asin-grid"]');
                    const footer=g && g.querySelector('[data-testid="load-more-footer"]');
                    const c=g && g.querySelectorAll('[data-testid="product-card"]');
                    if(footer)footer.scrollIntoView({block:'center',behavior:'instant'});
                    else if(c && c.length)c[c.length-1].scrollIntoView({block:'end',behavior:'instant'}); }""")
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
        if (item is None or self.current_document_url!=source['actual_source_url']
                or not same_loading_view(self.session.page.url,source['actual_source_url'])):return False
        body=Path(proof['html_path']).read_text(encoding='utf8')
        if body!=response.text:return False
        content_type=next((v for k,v in response.headers.items() if k.lower()=='content-type'),'application/json')
        item['route'].fulfill(status=200,body=body,headers={'content-type':content_type});item['delivered']=True
        self.loading_events.append({'origin':'LOCAL_UI_REPLAY','new_network_request':False,
            'request_id':receipt['request_id'],'request_url':key[0],
            'receipt_reference':{'path':receipt['evidence_path'],'sha256':hashlib.sha256(Path(receipt['evidence_path']).read_bytes()).hexdigest()}})
        self._last_dynamic_receipt=receipt
        return True

    def has_pending_dynamic(self,source):
        return any(not x.get('prepared') and x['value']['root_page_url']==source['actual_source_url'] for x in self.dynamic)

    def cancel_dynamic(self):
        for item in self.dynamic:
            if not item.get('delivered'):
                try:item['route'].abort()
                except Exception:pass
        self.dynamic.clear()
