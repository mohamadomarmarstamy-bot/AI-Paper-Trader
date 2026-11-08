"""Server-only RPC selection. Credentials never appear in public diagnostics."""
import os
from urllib.parse import urlsplit, urlunsplit

PUBLIC_HTTP = 'https://api.mainnet-beta.solana.com'


def validate(value, scheme):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != scheme or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or any(c.isspace() for c in value)):
            raise ValueError()
        parsed.port
        return value
    except (ValueError, TypeError):
        raise ValueError('Invalid server RPC endpoint configuration.') from None


def execution_source():
    if os.getenv('SOLANA_REAL_RPC_URL', '').strip():
        return 'dedicated_execution'
    if os.getenv('SOLANA_COPY_RPC_URL', '').strip():
        return 'configured_wallet_provider'
    return 'public_fallback'


def execution_url():
    value = (os.getenv('SOLANA_REAL_RPC_URL', '').strip()
             or os.getenv('SOLANA_COPY_RPC_URL', '').strip() or PUBLIC_HTTP)
    return validate(value, 'https')


def notifications_url():
    explicit = os.getenv('SOLANA_COPY_WS_URL', '').strip()
    if explicit:
        return validate(explicit, 'wss')
    # Keep the same host, path and credentials; do not guess a different vendor.
    value = validate(os.getenv('SOLANA_COPY_RPC_URL', '').strip() or PUBLIC_HTTP, 'https')
    parsed = urlsplit(value)
    return urlunsplit(('wss', parsed.netloc, parsed.path, parsed.query, ''))
