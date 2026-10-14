"""Pure evidence-backed source navigation shared by the link-first frontend.

Only the existing URL/category primitives are reused; no ranking or identity
pipeline runs here. No sibling list URL is synthesized.
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup
from ..multisource.contracts import RESEARCH_CATEGORIES, SOURCE_ROOTS
from ..multisource.sources import (browse_node_id, normalize_source_url,
    same_source_category, source_id, source_type_from_url, source_type_value)
from ..multisource.storage import digest


def discover_sources(html, source_type, page_url, *, research_mapping=None,
                     evidence_path=None, observed_at=None, origin="OFFLINE_FIXTURE", soup=None):
    source_type = source_type_value(source_type)
    tree = soup if soup is not None else BeautifulSoup(html or "", "lxml")
    navigation = tree.select("#zg_browseRoot, .zg-browse-root, .zg-browse-group, "
        "[class*='p13n-zg-nav-tree-all_style_zg-browse-root__'], "
        "[class*='p13n-zg-nav-tree-all_style_zg-browse-group__']")
    anchors = [a for nav in navigation for a in nav.select("a[href]")]
    anchors.extend(tree.select("a[data-category-link='true'][href]"))
    # Landing list carousels expose real category "Ver más" source anchors.
    # A category need not appear in a sidebar tree to be valid navigation.
    anchors.extend(tree.select("[data-card-metrics-id^='p13n-zg-list-carousel'] a[href]"))
    breadcrumbs = [n.get_text(" ", strip=True) for n in tree.select(
        "[aria-label='breadcrumb'] a, [aria-label='Breadcrumb'] a, .a-breadcrumb a")]
    result, seen = [], set()
    sha = digest(html.encode("utf-8"))
    for anchor in anchors:
        raw = str(anchor.get("href") or "")
        if not raw.strip() or raw.startswith("#"):
            continue
        url = normalize_source_url(raw, base_url=page_url)
        label = anchor.get_text(" ", strip=True)
        if (source_type_from_url(url) != source_type or url in seen or not label
                or label.isdigit() or anchor.find_parent(class_=re.compile('pagination'))):
            continue
        seen.add(url)
        wrapper = anchor.find_parent(attrs={"data-card-metrics-id": re.compile(r"^p13n-zg-list-carousel")})
        heading = wrapper.select_one("h2, h3") if wrapper else None
        category_label = heading.get_text(" ", strip=True) if heading else label
        mapped = (research_mapping or {}).get(url) or (research_mapping or {}).get(browse_node_id(url))
        if mapped is not None and (not isinstance(mapped, dict)
                or mapped.get("research_category") not in RESEARCH_CATEGORIES or not mapped.get("evidence")):
            raise ValueError("EXPLICIT_RESEARCH_MAPPING_EVIDENCE_REQUIRED")
        research = mapped.get("research_category") if mapped else None
        result.append({"source_id": source_id(source_type, url, research), "source_type": source_type,
            "root_url": SOURCE_ROOTS[source_type], "actual_source_url": url,
            "research_category": research, "amazon_category": category_label,
            "browse_node_id": browse_node_id(url), "category_path": breadcrumbs + [category_label],
            "source_status": "UNVERIFIED", "enabled": False, "mapping_evidence": mapped,
            "discovery_evidence": [{"kind": "NAVIGATION_LINK", "raw_href": raw,
                "page_url": page_url, "label_raw": label, "category_heading_raw": category_label, "evidence_path": evidence_path,
                "evidence_sha256": sha, "observed_at": observed_at, "origin": origin}]})
    return result


def source_coverage(sources):
    """Honest logical 75-cell status; unverified navigation is never availability."""
    return [{"source_type": kind, "research_category": category,
        "source_count": len(rows := [s for s in sources if s.get("source_type") == kind
            and s.get("research_category") == category]),
        "status": "AVAILABLE" if any(s.get("source_status") == "AVAILABLE"
            and s.get("verification_evidence") for s in rows) else "UNVERIFIED",
        "source_ids": [s["source_id"] for s in rows]}
        for kind in SOURCE_ROOTS for category in RESEARCH_CATEGORIES]


coverage_matrix = source_coverage


def detailed_coverage_matrix(sources, pages=(), observations=()):
    """75-cell scoped evidence report, preserving historical versus current.

    Inputs are parsed/audited records from one batch. This never sends requests,
    marks a source currently AVAILABLE, or treats failure as verified EMPTY.
    Target candidate identities must be supplied as target_group_id by the
    current normalizer; raw URL count is intentionally not an identity fallback.
    """
    sources=list(sources); pages=list(pages); observations=list(observations)
    matrix=[]
    for kind in SOURCE_ROOTS:
        for category in RESEARCH_CATEGORIES:
            scoped=[s for s in sources if s.get('source_type')==kind and s.get('research_category')==category]
            ids={s['source_id'] for s in scoped}
            ps=[p for p in pages if p.get('source_id') in ids]
            os=[o for o in observations if o.get('source_id') in ids]
            verified=[p for p in ps if p.get('access_status')=='NORMAL'
                      and p.get('source_type_verified') is True and p.get('source_context_valid') is True
                      and p.get('evidence_path') and p.get('evidence_sha256')]
            # An explicit source_empty parser decision is separate from failures.
            empty=[p for p in verified if p.get('source_empty') is True
                   and p.get('source_empty_evidence')]
            with_cards=[p for p in verified if int(p.get('valid_link_count') or 0)>0]
            completed=[p for p in verified if p.get('status')=='COMPLETE'
                       and p.get('page_complete') is True and p.get('complete_evidence')
                       and not p.get('flags')]
            if with_cards:
                status='HISTORICALLY_OBSERVED'; reason=None
            elif empty and len(empty)==len(ps):
                status='HISTORICALLY_VERIFIED_EMPTY'; reason='EXPLICIT_SOURCE_EMPTY_EVIDENCE'
            else:
                status='UNVERIFIED'
                reason='NO_DISCOVERED_MAPPED_SOURCE' if not scoped else 'NOT_ATTEMPTED' if not ps else 'NO_VERIFIED_PRODUCT_CARDS'
            matrix.append({'source_type':kind,'research_category':category,
                'discovered_source_count':len(scoped),'verified_source_count':len({p['source_id'] for p in verified}),
                'completed_page_count':len(completed),'extracted_observation_count':len(os),
                'unique_link_candidate_count':len({o['target_group_id'] for o in os if o.get('target_group_id')
                    and o.get('link_valid') is True}),
                'source_status':status,'current_availability_verified':False,
                'source_evidence':[{'source_id':s['source_id'],'discovery_evidence':s.get('discovery_evidence',[])} for s in scoped]
                    +[{'page_url':p.get('page_url'),'evidence_path':p.get('evidence_path'),
                       'evidence_sha256':p.get('evidence_sha256'),'observed_at':p.get('observed_at'),
                       'origin':p.get('origin'),'status':p.get('status')} for p in ps],
                'missing_reason':reason,'identity_count_missing':sum(1 for o in os if o.get('link_valid') is True and not o.get('target_group_id'))})
    return matrix
