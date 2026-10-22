"""One explicit destination setup attempt in a reusable collection profile."""
import hashlib
from pathlib import Path
from ..access.browser import BrowserSession
from ..multisource.storage import immutable_bytes, immutable_json


def configure_delivery(profile_dir, output, *, postal_code='28001', session_factory=BrowserSession):
    profile=Path(profile_dir).resolve()
    root=Path(output).resolve()
    root.mkdir(parents=True,exist_ok=False)
    with session_factory(headless=False,profile_dir=str(profile)) as session:
        error=None
        try:
            location=session.ensure_spain_delivery(postal_code)
        except Exception as exc:
            error=str(exc)
        html,diagnostics=session.delivery_diagnostics()
        body=html.encode('utf8')
        immutable_bytes(root/'delivery_page.html',body)
        result={**diagnostics,'status':'BLOCKED' if error else 'VERIFIED',
                'error':error,'profile_dir':str(profile),'requested_postal_code':postal_code,
                'evidence_path':str(root/'delivery_page.html'),'evidence_sha256':hashlib.sha256(body).hexdigest(),
                'automatic_retry':False,'candidate_collection_started':False}
        immutable_json(root/'delivery_gate.json',result)
        if error:
            raise ValueError('DELIVERY_PREFLIGHT_STOPPED; evidence='+str(root/'delivery_gate.json')+'; '+error)
        return result
