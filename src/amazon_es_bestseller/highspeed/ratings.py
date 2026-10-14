"""Card-bound consumer ratings, preserving raw Spanish and per-node proof.

No detail access, star-class inference, default rating, or product filtering.
"""
from __future__ import annotations
import hashlib
import re
from datetime import datetime
from bs4 import Tag

RATING_PARSER_VERSION = 'card_ratings_v3.1'
_CARD = "#gridItemRoot, .zg-grid-general-faceout, [id^='p13n-asin-index-'], [data-ranking-card='true'], .ranking-card, li.zg-item-immersion"
_RATING = re.compile(r'(?<![\d.,+-])(\d+(?:[.,]\d+)?)\s*(?:de|out of)\s*5\s*(?:estrellas|stars)',re.I)
_COUNT = re.compile(r'(?<![\d.,+-])([\d][\d. ,\u00a0\u202f]*)\s*(?:calificaciones|valoraciones|reseñas|resenas|reviews|ratings)\b',re.I)

def _sha(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()

def _target(href):
    m=re.search(r'/(?:dp|gp/product|product-reviews)/([A-Z0-9]{10})(?:[/?]|$)',href or '')
    return m.group(1) if m else None

def _count(raw):
    value=raw.strip().replace('\u00a0',' ').replace('\u202f',' ')
    # Spanish separators require complete groups, never truncate a decimal.
    if re.fullmatch(r'\d+',value): return int(value)
    if re.fullmatch(r'\d{1,3}(?:[. ]\d{3})+',value): return int(re.sub(r'[. ]','',value))
    return None

def _owned(node,card):
    for p in [node,*node.parents]:
        if p is card: return True
        classes=' '.join(p.get('class',[])).lower()
        if any(x in classes for x in ('sponsored','advertisement','ad-holder')) or p.get('data-ad-id'): return False
        # Nested ranked/foreign cards cannot contribute to current card.
        if p is not node and (p.get('id')=='gridItemRoot' or p.get('data-ranking-card')=='true' or bool(set(p.get('class',[])) & {'ranking-card','a-carousel-card','zg-item-immersion'})): return False
    return False

def extract_rating_fields(card, *, raw_product_url, evidence_path, evidence_sha256,
                          observed_at, representation, representation_slot):
    """Extract within one supplied actual card. Caller owns page/card selection.

    Evidence includes exact card hash, raw href, representation slot and rank.
    A missing field is NOT_DISPLAYED only for a verified, ranked card. Truncated
    card markup or invalid binding is UNVERIFIED, never silently missing.
    """
    rank=card.select_one('.zg-bdg-text, span.a-badge-text, [data-ranking-position]')
    rank_raw=rank.get_text(' ',strip=True) if rank else None
    rank_value=rank.get('data-ranking-position') if rank else None
    ranked=bool(re.fullmatch(r'#\s*\d+',rank_raw or '') or (str(rank_value).isdigit() and int(rank_value)>0))
    urls=[a.get('href') for a in card.select('a[href]') if _owned(a,card)]
    # An exact raw URL is mandatory. ASIN is only used to reject wrong review links.
    bound=raw_product_url in urls
    try:
        dt=datetime.fromisoformat(observed_at.replace('Z','+00:00'))
        time_valid=dt.tzinfo is not None
    except (TypeError,ValueError,AttributeError):time_valid=False
    marked = card.get('id') == 'gridItemRoot' or str(card.get('id','')).startswith('p13n-asin-index-') or card.get('data-ranking-card') == 'true' or bool(set(card.get('class',[])) & {'ranking-card','zg-grid-general-faceout','zg-carousel-general-faceout','a-carousel-card','zg-item-immersion'}) or card.name == 'li'
    ad_card = bool(card.get('data-ad-id')) or any(x in ' '.join(card.get('class',[])).lower() for x in ('sponsored','advertisement','ad-holder'))
    verified=marked and not ad_card and bound and ranked and time_valid and bool(evidence_path) and bool(re.fullmatch('[0-9a-f]{64}',evidence_sha256 or '')) and isinstance(representation_slot,int) and representation_slot>=0
    context={'raw_product_url':raw_product_url,'representation':representation,
        'representation_slot':representation_slot,'rank_raw':rank_raw,'evidence_path':str(evidence_path),
        'evidence_sha256':evidence_sha256,'card_sha256':_sha(str(card)), 'parser_version':RATING_PARSER_VERSION}
    result={'rating_raw':None,'rating_value':None,'rating_status':'NOT_DISPLAYED' if verified else 'UNVERIFIED',
        'rating_source':None,'rating_evidence':[], 'review_count_raw':None,'review_count':None,
        'review_count_status':'NOT_DISPLAYED' if verified else 'UNVERIFIED','review_count_evidence':[],
        'rating_observed_at':observed_at,'rating_parser_version':RATING_PARSER_VERSION,
        'rating_binding_evidence':context}
    if not verified:return result
    candidates={'rating':[],'review_count':[]}
    def add(field,node,selector,index,raw,value,attr=None):
        proof={**context,'selector':selector,'selector_match_index':index,'attribute':attr,
            'text_raw':raw,'node_sha256':_sha(str(node))}
        candidates[field].append((raw,value,proof))
    target=_target(raw_product_url)
    selectors=('a[aria-label]', '.a-icon-alt', '[data-rating-text]', '[data-review-count]', '.review-count', "a[href*='/product-reviews/'] .a-size-small")
    for selector in selectors:
        for index,node in enumerate(card.select(selector)):
            if not _owned(node,card):continue
            anchor=node if node.name=='a' else node.find_parent('a',href=True)
            if anchor and '/product-reviews/' in anchor.get('href','') and (target is None or _target(anchor['href'])!=target):
                # Explicit foreign target: do not accept these fields as current product.
                result['rating_status']=result['review_count_status']='UNVERIFIED'
                continue
            if selector == 'a[aria-label]' and not (('/product-reviews/' in node.get('href','')) or node.select_one('.a-icon-alt')):
                continue
            if any('histogram' in ' '.join(p.get('class',[])) or p.get('id','').startswith('histogram') for p in [node,*node.parents] if isinstance(p,Tag)):
                continue
            attr='aria-label' if selector=='a[aria-label]' else 'data-rating-text' if selector=='[data-rating-text]' else 'data-review-count' if selector=='[data-review-count]' else None
            raw=str(node.get(attr) or '') if attr else node.get_text(' ',strip=True)
            if '%' in raw:
                continue
            rm=_RATING.search(raw)
            cm=_COUNT.search(raw)
            if rm:
                number=float(rm.group(1).replace(',','.'))
                add('rating',node,selector,index,rm.group(0),number if 0<=number<=5 else None,attr)
            elif selector in ('.a-icon-alt','[data-rating-text]') or re.search(r'estrellas|stars',raw,re.I):
                add('rating',node,selector,index,raw,None,attr)
            if cm:add('review_count',node,selector,index,cm.group(0),_count(cm.group(1)),attr)
            elif re.search(r'calificaciones|valoraciones|reseñas|resenas|reviews|ratings',raw,re.I):
                add('review_count',node,selector,index,raw,None,attr)
            elif selector in ('[data-review-count]','.review-count',"a[href*='/product-reviews/'] .a-size-small"):
                add('review_count',node,selector,index,raw,_count(raw),attr)
    for field,items in candidates.items():
        if result[field+'_status']=='UNVERIFIED':continue
        if not items:continue
        values={i[1] for i in items if i[1] is not None}
        malformed=any(i[1] is None for i in items)
        status='SOURCE_CONFLICT' if len(values)>1 else 'PARSE_FAILED' if malformed else 'VALID'
        result[field+'_status']=status
        result[field+'_raw']=items[0][0]
        result['rating_value' if field=='rating' else 'review_count']=next(iter(values)) if status=='VALID' else None
        result[field+'_evidence']=[i[2] for i in items]
    if result['rating_evidence']:result['rating_source']='RANKING_CARD'
    return result

def validate_rating_fields(fields,card):
    """Recompute all values and DOM proofs; hashes alone do not prove a rating."""
    try:
        b=fields['rating_binding_evidence']
        expected=extract_rating_fields(card,raw_product_url=b['raw_product_url'],evidence_path=b['evidence_path'],
            evidence_sha256=b['evidence_sha256'],observed_at=fields['rating_observed_at'],
            representation=b['representation'],representation_slot=b['representation_slot'])
        return all(fields.get(k)==v for k,v in expected.items())
    except (KeyError,TypeError,ValueError):return False
