from amazon_es_bestseller.access_detector import detect_access_state
from amazon_es_bestseller.models import AccessState


def test_detects_robot_check_from_title_and_body():
    result = detect_access_state("Robot Check", "To discuss automated access...")
    assert result.state is AccessState.CHALLENGE


def test_detects_rate_limit_from_http_status():
    result = detect_access_state("Amazon.es", "", http_status=429)
    assert result.state is AccessState.RATE_LIMITED


def test_normal_page_has_no_stop_reason():
    result = detect_access_state("Amazon.es: compra online", "<main>content</main>", 200)
    assert result.state is AccessState.NORMAL
    assert result.reason is None
