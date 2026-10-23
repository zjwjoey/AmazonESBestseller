"""Saved Customer Favorites card/link identity, without ranking completeness claims."""
import hashlib
from pathlib import Path
from bs4 import BeautifulSoup
from ..access.detector import detect_access_status
from ..candidate_sources.v4_sources import source_for_url
from ..candidate_sources.v4_adapters import parse_v4_page
from .ranking_identity.resolver import resolve_candidates
from .ranking_identity.urls import asin_from_product_url


def verify_favorite_links(candidates):
    records, rejected, pages = [], [], {}
    for candidate in candidates:
        try:
            if not candidate.get('candidate_id') or not candidate.get('observation_id'):
                raise ValueError('CANDIDATE_PROVENANCE_REQUIRED')
            path = Path(candidate['evidence_path'])
            key = (str(path), candidate['evidence_sha256'], candidate['source_url'])
            if key not in pages:
                body = path.read_bytes()
                if hashlib.sha256(body).hexdigest() != candidate['evidence_sha256']:
                    raise ValueError('SOURCE_HASH_MISMATCH')
                html = body.decode('utf-8', errors='replace')
                if detect_access_status(200, html).value != 'NORMAL':
                    raise ValueError('SOURCE_ACCESS_RESTRICTED')
                source = source_for_url(candidate['source_url'], source_type='CUSTOMER_FAVORITES')
                parsed = parse_v4_page(html, source=source, page_url=candidate['source_url'],
                    run_id='identity_replay', snapshot_id='identity_replay', observed_at='2026-10-23T00:00:00+00:00',
                    evidence_path=str(path), http_status=200, origin='OFFLINE_FIXTURE')
                soup = BeautifulSoup(html, 'lxml')
                cards = {hashlib.sha256(str(card).encode()).hexdigest():card
                         for card in soup.select('[data-component-type="s-search-result"][data-asin], [data-cy="asin-faceout-container"]')}
                pages[key] = (parsed['observations'], cards)
            observations, cards = pages[key]
            matches = []
            for row in observations:
                proof = row.get('source_evidence') or {}
                card = cards.get(proof.get('card_sha256'))
                if card is None: continue
                asin = str(card.get('data-asin') or '').upper()
                raw = row.get('raw_product_url') or ''
                if asin != candidate['asin_hint'] or asin_from_product_url(raw) != asin: continue
                if row.get('binding_doubts') or not row.get('is_valid'): continue
                resolved, _ = resolve_candidates([{'asin':asin,'card_asin':asin,'href_asin':asin,
                    'raw_href':raw,'asin_source':'CARD_DATA_ASIN','evidence_source':'SERVER_RENDERED_CARD',
                    'source_url':candidate['source_url'],'evidence_file':str(path)}])
                if resolved and resolved[0]['identity_status'] == 'CONFIRMED': matches.append((resolved[0],row))
            if len(matches) != 1: raise ValueError('CARD_LINK_IDENTITY_MISSING_OR_AMBIGUOUS')
            identity, row = matches[0]
            records.append({**identity, 'ranking_asin':candidate['asin_hint'],
                'candidate_id':candidate['candidate_id'], 'candidate_observation_id':candidate['observation_id'],
                'ranking_link_identity_status':'MATCH', 'ranking_product_url_raw':row['raw_product_url'],
                'ranking_product_url_normalized':row['normalized_product_url'],
                'ranking_source_type':'CUSTOMER_FAVORITES','ranking_source_url':candidate['source_url'],
                'research_category':candidate['category_l1'],'bestseller_rank':None,'ranking_rank':None,
                'candidate_evidence_file_path':str(path),'candidate_evidence_sha256':candidate['evidence_sha256']})
        except (ValueError, KeyError, OSError) as exc:
            rejected.append({'candidate_id':candidate.get('candidate_id'),'reason':str(exc)})
    return {'snapshot_kind':'CANDIDATE_LINK_IDENTITY', 'snapshot_status':'LINK_IDENTITY_VERIFIED' if records and not rejected else 'BLOCKED',
            'authoritative':False,'identity_complete':False,'strict_buyable_confirmed':False,
            'candidates':candidates,'records':records,'rejected':rejected}
