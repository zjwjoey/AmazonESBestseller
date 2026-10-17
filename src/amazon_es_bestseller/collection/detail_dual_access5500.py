"""Full-document, equivalent fast negative path for the isolated dual runtime.

Every original regex alternative implies at least one complete-document literal
below after Unicode case folding. A possible hit always runs the original regex;
a changed regex/flags always falls back. Nothing is truncated or accepted from a
positive prefilter. The shared production detector and field parser are unchanged.
"""
import hashlib
import re
from ..access import detector as original

_EXPECTED_PATTERN_SHA256 = '584a8c41cd662107ecc55e9949b3fcedb11d6e14faf43d66efdb10dd22796a5e'
_NEEDLES=('captcha','type the characters','robot check','access den',
          'unusual traff','rate exceeded','n de abajo para segu')


def detect_access_status(status_code, page_text=''):
    regex=original.CAPTCHA_RE
    if (not isinstance(page_text,str) or not page_text
            or regex.flags != re.IGNORECASE | re.UNICODE
            or hashlib.sha256(regex.pattern.encode('utf8')).hexdigest()!=_EXPECTED_PATTERN_SHA256):
        return original.detect_access_status(status_code,page_text)
    # Full string scan. Needle fragments deliberately omit i in denied/traffic:
    # re.IGNORECASE also accepts dotted/dotless I; casefold alone does not.
    # Long s and Kelvin K are handled by casefold for the other literal spans.
    folded=page_text.casefold()
    if any(needle in folded for needle in _NEEDLES):
        return original.detect_access_status(status_code,page_text)
    return original.detect_access_status(status_code,'')
