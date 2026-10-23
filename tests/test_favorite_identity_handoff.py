import hashlib
from pathlib import Path
import pytest
from amazon_es_bestseller.monitoring.favorite_identity import verify_favorite_links
from amazon_es_bestseller.monitoring.detail_planner import build_detail_plan

FIXTURE=Path(__file__).parent/"fixtures/html/favorite_identity_saved_card.html"
def candidate(path=FIXTURE):
 return {"evidence_path":str(path),"evidence_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"source_url":"https://www.amazon.es/s?i=kitchen&srs=88626813031&rh=n%3A88626813031&s=popularity-rank&fs=true&ref=lp_88626813031_sar","asin_hint":"B07ZHPV9JW","candidate_id":"original_candidate","observation_id":"original_observation","category_l1":"Hogar y cocina"}

def test_real_saved_card_enters_detail_plan_without_completeness_or_buyable_claims():
 selection=verify_favorite_links([candidate()])
 assert selection["snapshot_status"]=="LINK_IDENTITY_VERIFIED"
 assert not selection["authoritative"] and not selection["identity_complete"] and not selection["strict_buyable_confirmed"]
 plan=build_detail_plan(selection)
 row=plan["records"][0]
 assert row["asin"]=="B07ZHPV9JW" and row["candidate_ids"]==["original_candidate"]
 assert row["candidate_observation_ids"]==["original_observation"]
 assert row["ranking_rank"] is None and row["detail_action"]=="FETCH_NEW"

@pytest.mark.parametrize("fault",["hash","asin","source","provenance"])
def test_invalid_inputs_cannot_be_promoted(fault):
 c=candidate()
 if fault=="hash":c["evidence_sha256"]="0"*64
 if fault=="asin":c["asin_hint"]="B000000001"
 if fault=="source":c["source_url"]="https://example.com/s?i=kitchen"
 if fault=="provenance":c["candidate_id"]=""
 result=verify_favorite_links([c])
 assert result["snapshot_status"]=="BLOCKED"
 with pytest.raises(ValueError):build_detail_plan(result)

def test_conflicting_card_href_rejected(tmp_path):
 p=tmp_path/"conflict.html"
 p.write_text(FIXTURE.read_text(encoding="utf-8").replace('data-asin="B07ZHPV9JW"','data-asin="B000000001"'),encoding="utf-8")
 assert verify_favorite_links([candidate(p)])["rejected"]

def test_plan_rechecks_evidence_after_selection(tmp_path):
 p=tmp_path/"page.html";p.write_bytes(FIXTURE.read_bytes())
 selection=verify_favorite_links([candidate(p)])
 p.write_text("Robot Check",encoding="utf-8")
 with pytest.raises(ValueError):build_detail_plan(selection)
