"""Fail before writes when Windows cannot publish immutable journal filenames.

V2 uses readable ordinary absolute paths, without registry or OS configuration
changes. The conservative bound leaves room for content hashes and atomic
temporary filenames. Existing production storage rules are untouched.
"""
import os
from pathlib import Path

def _utf16_length(value):
    return len(str(value).encode("utf-16-le")) // 2

def validate_store_root(root):
    resolved = Path(root).resolve()
    if os.name == "nt" and _utf16_length(resolved) > 170:
        raise ValueError("SHORTER_ROOT_REQUIRED: Windows V2 store root exceeds 170 UTF-16 characters; choose a shorter runtime directory before retrying")
    return resolved

def validate_run_storage_root(root, run_id):
    resolved = validate_store_root(root)
    # Reserved receipts are the longest fixed request journal names. Account
    # for mkstemp's prefix dot and random suffix, rather than only final files.
    longest = resolved / "runs" / str(run_id) / "requests" / (".req_"+"0"*32+".reserved.json."+"0"*8)
    if os.name == "nt" and _utf16_length(longest) >= 259:
        raise ValueError("SHORTER_ROOT_REQUIRED: Windows run journal path is too long; shorten the runtime root or run ID before retrying")
    return resolved
