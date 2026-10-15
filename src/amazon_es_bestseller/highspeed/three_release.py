"""Derived NEW release signals from the same product card, never page headings."""
from collections import defaultdict
from datetime import date
from functools import lru_cache
import hashlib,re
from pathlib import Path
from bs4 import BeautifulSoup
from .adapters import _cards
from .budgets import aware_time

MONTHS={'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,
    'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}


def classify_card(card,observed_at):
    signals=[];dates=[];preorder=False;available=False
    for n in card.select('span, p'):
        if n.select_one('span, p') or n.find_parent('a',href=True):continue
        text=n.get_text(' ',strip=True)
        if re.match(r'^(?:Reservar(?: ahora)?|Preventa|Precompra|Pre-order|Disponible (?:el|a partir del)|'
                    r'Fecha de lanzamiento:|Este producto saldr[aá]|This item will be released on|En stock\b|In stock\b)',text,re.I):
            signals.append({'raw':text,'node_sha256':hashlib.sha256(str(n).encode('utf8')).hexdigest()})
            preorder=preorder or bool(re.match(r'^(Reservar|Preventa|Precompra|Pre-order)',text,re.I))
            available=available or bool(re.match(r'^(En stock|In stock)',text,re.I))
            m=re.search(r'(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})',text,re.I)
            iso=re.search(r'\b(\d{4}-\d{2}-\d{2})\b',text)
            try:
                d=date(int(m[3]),MONTHS[m[2].lower()],int(m[1])) if m else date.fromisoformat(iso[1]) if iso else None
                if d:dates.append(d)
            except (ValueError,KeyError):pass
    future=any(d>aware_time(observed_at).date() for d in dates)
    status=('SOURCE_CONFLICT' if available and (future or preorder) else
        'FUTURE_RELEASE_VISIBLE' if future else 'PREORDER_VISIBLE' if preorder else
        'AVAILABLE_NOW_VISIBLE' if available else 'SCHEDULED_DATE_NOT_CONFIRMED_FUTURE' if dates else
        'RELEASE_SIGNAL_DATE_UNPARSED' if signals else 'NOT_DISPLAYED')
    return {'release_status':status,'release_signals_raw':[s['raw'] for s in signals],
        'release_declared_dates':[d.isoformat() for d in dates],'release_node_evidence':signals,
        'release_semantics':'VISIBLE_CARD_SIGNAL_NOT_CONFIRMED_BUYABLE_SKU'}


def release_signals(observations):
    @lru_cache(maxsize=8)
    def document(path,sha):
        body=Path(path).read_bytes()
        if hashlib.sha256(body).hexdigest()!=sha:raise ValueError('THREE_RELEASE_DOM_SHA_MISMATCH')
        cards=_cards(BeautifulSoup(body,'lxml'),fragment=True);lookup=defaultdict(list)
        for i,card in enumerate(cards):
            for href in {a['href'] for a in card.select('a[href]')}:lookup[href].append(i)
        return cards,lookup
    result={}
    for row in observations:
        if row['source_type']!='NEW_RELEASES':continue
        cards,lookup=document(row['evidence_path'],row['evidence_sha256'])
        matches=lookup.get(row.get('raw_product_url'),[]);slot=row.get('representation_slot')
        index=slot if slot in matches else matches[0] if len(matches)==1 else None
        if index is None:
            value={'release_status':'UNVERIFIED_CARD_BINDING','release_signals_raw':[],
                'release_declared_dates':[],'release_node_evidence':[]}
        else:value=classify_card(cards[index],row['observed_at'])
        result[row['observation_id']]={**value,'observation_id':row['observation_id'],
            'evidence_path':row['evidence_path'],'evidence_sha256':row['evidence_sha256']}
    return result


def annotate_release(candidates,signals):
    for candidate in candidates:
        own=signals.get(candidate['observation_id'])
        candidate['release_status']=own['release_status'] if own else 'NOT_APPLICABLE_TO_REPRESENTATIVE'
        candidate['release_evidence_by_observation']=[signals[r['observation_id']]
            for r in candidate['source_memberships'] if r['observation_id'] in signals]
    return candidates
