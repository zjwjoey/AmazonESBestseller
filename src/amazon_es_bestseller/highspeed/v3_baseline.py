"""Read-only historical CSV/evidence baseline; never represents a new capture."""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import uuid
from ..multisource.contracts import SOURCE_ROOTS, RESEARCH_CATEGORIES

def baseline_review(csv_path, *, candidate_jsonl=None):
    path=Path(csv_path)
    with path.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle);fields=reader.fieldnames;rows=list(reader)
    required={'商品名称（西班牙语）','商品链接（规范化）','来源榜单','来源页面','研究类目（未确证留空）','候选组ID'}
    if not required.issubset(fields or []):raise ValueError('BASELINE_CSV_SCHEMA_CONFLICT')
    by_type=Counter();pages=defaultdict(set);mapping=Counter();unmapped_sources=Counter()
    names=Counter();urls=Counter();groups=Counter();tracking=0
    for row in rows:
        kind=row['来源榜单'];by_type[kind]+=1;pages[kind].add(row['来源页面'])
        category=row['研究类目（未确证留空）'];mapping[category or 'UNMAPPED']+=1
        if not category:unmapped_sources[row['来源页面']]+=1
        name=row['商品名称（西班牙语）'].strip()
        names['nonempty' if name else 'empty']+=1
        if re.fullmatch(r'\s*(?:[€$£]\s*)?\d+[\d.,\s]*(?:[€$£]|EUR|USD|GBP)\s*',name,re.I):names['price_only']+=1
        url=row['商品链接（规范化）'];urls[url]+=1;groups[row['候选组ID']]+=1
        if '/ref=' in url or re.search(r'/\d{3}-\d{7}-\d{7}(?:[/?]|$)',url):tracking+=1
    result={'historical_input':True,'live_requests':0,'csv_reference':{'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
        'csv_rows':len(rows),'csv_fields':fields,'by_source_type':[{ 'source_type':k,'candidate_rows':by_type[k],'actual_source_pages':len(pages[k])} for k in SOURCE_ROOTS],
        'research_category_counts':[{ 'research_category':k,'candidate_rows':mapping[k]} for k in RESEARCH_CATEGORIES],
        'unmapped_candidate_rows':mapping['UNMAPPED'],'unmapped_sources':dict(unmapped_sources),'title_counts':dict(names),
        'csv_exact_url_duplicate_rows':sum(n-1 for n in urls.values()),'csv_duplicate_group_rows':sum(n-1 for n in groups.values()),
        'csv_rows_with_ref_or_session_path':tracking,'missing_csv_fields':['rating_raw','rating_value','rating_status','review_count_raw','review_count','review_count_status','current_price','original_price'],
        'identity_warning':'URL candidates do not prove independent purchasable SKUs. CSV duplicate counts do not settle destination identity.',
        'denominator_scope':'30k historical CSV candidate representatives; not all ranking observations or actual network requests.'}
    if candidate_jsonl:
        body_refs={};seen=0;available=0;sha_ok=0;dedup=Counter();binding_failures=0
        with Path(candidate_jsonl).open(encoding='utf-8') as source:
            for index,line in enumerate(source):
                value=json.loads(line);seen+=1;dedup[value.get('dedup_status') or 'UNKNOWN']+=1
                if index>=len(rows) or value.get('normalized_product_url')!=rows[index]['商品链接（规范化）'] or value.get('product_title_for_research')!=rows[index]['商品名称（西班牙语）']:binding_failures+=1
                key=(value.get('evidence_path'),value.get('evidence_sha256'))
                if key not in body_refs:
                    item=Path(key[0]) if key[0] else None
                    exists=bool(item and item.is_file())
                    body_refs[key]=(exists,exists and hashlib.sha256(item.read_bytes()).hexdigest()==key[1])
                accessible,valid_sha=body_refs[key];available+=accessible;sha_ok+=valid_sha
        result['saved_evidence']={'candidate_jsonl':str(Path(candidate_jsonl).resolve()),'jsonl_rows':seen,'csv_alignment_failures':binding_failures,'candidate_rows_with_local_body':available,'candidate_rows_with_valid_sha':sha_ok,'unique_body_references':len(body_refs),'denominator':seen,'scope':'Saved original HTML reachability and hash only; not current website availability.'}
        result['historical_dedup_statuses']=dict(dedup)
    return result

def export_baseline(csv_path,output_root,*,candidate_jsonl=None):
    result=baseline_review(csv_path,candidate_jsonl=candidate_jsonl)
    directory=Path(output_root)/('baseline_'+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
    (directory/'baseline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['V3开发历史基线审查','历史数据，不是本轮新官网采集。',f"候选行数：{result['csv_rows']}；未映射：{result['unmapped_candidate_rows']}。"]
    lines.extend(f"{r['source_type']}：{r['candidate_rows']}条，{r['actual_source_pages']}个实际来源页。" for r in result['by_source_type'])
    lines.append('旧CSV没有评分、评论数和完整价格字段；原HTML可恢复范围必须另做卡片证据复核。')
    (directory/'基线审查说明.txt').write_text('\n'.join(lines),encoding='utf-8')
    return {'directory':str(directory),'result':result}
