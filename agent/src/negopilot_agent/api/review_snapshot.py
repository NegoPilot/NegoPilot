"""Signed, expiring review snapshot: clients cannot change reviewed facts or provenance."""
import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

VERSION = 'p0.1'
TTL_SECONDS = 3600

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def input_hash(offer):
    return hashlib.sha256(canonical(offer)).hexdigest()

def sign_review(offer, assessment, evidence, key):
    snapshot = {'review_id': str(uuid4()), 'version': VERSION, 'input_hash': input_hash(offer),
                'issued_at': int(time.time()), 'expires_at': int(time.time()) + TTL_SECONDS,
                'offer': offer, 'assessment': assessment, 'evidence': evidence}
    encoded = base64.urlsafe_b64encode(canonical(snapshot)).decode()
    signature = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return {k: v for k, v in snapshot.items() if k not in ('offer', 'evidence')} | {
        'snapshot': {'offer': offer}, 'review_token': encoded + '.' + signature}

def verify_review(token, key):
    try:
        encoded, signature = token.rsplit('.', 1)
        expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        snapshot = json.loads(base64.urlsafe_b64decode(encoded))
        if snapshot['version'] != VERSION or snapshot['expires_at'] <= time.time():
            raise ValueError()
        if snapshot['input_hash'] != input_hash(snapshot['offer']):
            raise ValueError()
        return snapshot
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ValueError('Invalid or expired review snapshot') from None
