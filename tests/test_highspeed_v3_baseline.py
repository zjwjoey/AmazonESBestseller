import csv
import hashlib
import json
from amazon_es_bestseller.highspeed.v3_baseline import baseline_review

def test_baseline_separates_csv_claims_from_saved_evidence(tmp_path):
    body=tmp_path/'page.html';body.write_text('saved source',encoding='utf-8')
    headers=['商品名称（西班牙语）','商品链接（规范化）','来源榜单','来源页面','研究类目（未确证留空）','候选组ID']
    path=tmp_path/'old.csv'
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(headers);w.writerow(['Original','https://www.amazon.es/item','BEST_SELLERS','https://www.amazon.es/gp/bestsellers','', 'group'])
    evidence=tmp_path/'old.jsonl'
    evidence.write_text(json.dumps({'normalized_product_url':'https://www.amazon.es/item','product_title_for_research':'Original','evidence_path':str(body),'evidence_sha256':hashlib.sha256(body.read_bytes()).hexdigest(),'dedup_status':'POSSIBLE_DUPLICATE'})+'\n',encoding='utf-8')
    result=baseline_review(path,candidate_jsonl=evidence)
    assert result['live_requests']==0 and result['historical_input'] is True
    assert result['unmapped_candidate_rows']==1
    assert result['saved_evidence']['candidate_rows_with_valid_sha']==1
    assert result['saved_evidence']['csv_alignment_failures']==0
    body.write_text('changed',encoding='utf-8')
    assert baseline_review(path,candidate_jsonl=evidence)['saved_evidence']['candidate_rows_with_valid_sha']==0
