"""One explicit destination setup attempt in a reusable collection profile."""
import hashlib
from pathlib import Path
from ..access.browser import BrowserSession
from ..multisource.storage import immutable_bytes, immutable_json


def verify_session_delivery(session, output, *, postal_code='28001', profile_dir=None):
    """One normal UI setup, persisted separately from business HTTP receipts."""
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    error = None
    try:
        session.ensure_spain_delivery(postal_code)
    except Exception as exc:
        error = str(exc)
    try:
        html, diagnostics = session.delivery_diagnostics()
    except Exception as exc:
        html, diagnostics = '', {'http_status': None, 'access_status': 'UNKNOWN', 'delivery_is_spain': None,
                                  'diagnostics_error': type(exc).__name__ + ': ' + str(exc)}
        error = error or 'DELIVERY_DIAGNOSTICS_UNAVAILABLE'
    # A successful method return alone cannot assert verified delivery.
    if diagnostics.get('delivery_is_spain') is not True or diagnostics.get('access_status') != 'NORMAL':
        error = error or 'CURRENT_SPAIN_HEADER_AND_NORMAL_ACCESS_REQUIRED'
    body = html.encode('utf8')
    immutable_bytes(root / 'delivery_page.html', body)
    result = {**diagnostics, 'status': 'BLOCKED' if error else 'VERIFIED', 'error': error,
              'requested_postal_code': postal_code, 'evidence_path': str(root / 'delivery_page.html'),
              'evidence_sha256': hashlib.sha256(body).hexdigest(), 'automatic_retry': False,
              'candidate_collection_started': False, 'business_http_receipt': False}
    if profile_dir is not None:result['profile_dir']=str(Path(profile_dir).resolve())
    immutable_json(root / 'delivery_gate.json', result)
    if error:
        raise ValueError('DELIVERY_PREFLIGHT_STOPPED; evidence=' + str(root / 'delivery_gate.json') + '; ' + error)
    return result


def configure_delivery(profile_dir, output, *, postal_code='28001', session_factory=BrowserSession):
    profile=Path(profile_dir).resolve()
    with session_factory(headless=False,profile_dir=str(profile)) as session:
        return verify_session_delivery(session,output,postal_code=postal_code,profile_dir=profile)
