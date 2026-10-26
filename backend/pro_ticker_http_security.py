"""Strict browser origin checks using an optional server-owned HTTPS origin."""
import os
import re
from urllib.parse import urlsplit
from fastapi import HTTPException


def require_same_origin(request):
    expected = os.getenv('APP_PUBLIC_ORIGIN', '')
    if expected:
        # TLS may terminate at the edge while ASGI receives HTTP. Never choose
        # the public origin from client-supplied forwarding headers or Origin.
        try:
            parts = urlsplit(expected)
            valid = (parts.scheme == 'https' and parts.hostname
                     and not parts.username and not parts.password
                     and not parts.path and not parts.query and not parts.fragment
                     and expected == 'https://' + parts.netloc
                     and re.fullmatch(r'[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?', parts.hostname)
                     and (parts.port is None or 1 <= parts.port <= 65535)
                     and not any(ord(c) <= 32 or ord(c) >= 127 for c in expected)
                     and request.headers.get('host') == parts.netloc)
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(403, 'Same-origin request required.')
    else:
        expected = str(request.base_url).rstrip('/')
    if request.headers.get('origin') != expected:
        raise HTTPException(403, 'Same-origin request required.')
