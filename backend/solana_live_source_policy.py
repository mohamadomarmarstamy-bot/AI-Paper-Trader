"""Owner-selected paper-only sources. Applies to new live buys, never exits."""
from solana_paper_trials import CANDIDATES

PAPER_ONLY_SOURCES = {
    **{wallet: name for name, wallet in CANDIDATES},
    'GJA1HEbxGnqBhBifH9uQauzXSB53to5rhDrzmKxhSU65': 'Latuche',
    'CAPn1yH4oSywsxGU456jfgTrSSUidf9jgeAnHceNUJdw': 'Cap',
    '2kv8X2a9bxnBM8NKLc6BBTX2z13GFNRL4oRotMUJRva9': 'Gh0stee',
    '3H9LVHarjBoZ2YPEsgFbVD1zuERCGwfp4AeyHoHsFSEC': 'Jadawgs',
    '4Be9CvxqHW6BYiRAxW9Q3xu1ycTMWaL5z8NX4HR3ha7t': 'Mitch',
    'A3W8psibkTUvjxs4LRscbnjux6TFDXdvD4m4GsGpQ2KJ': 'Numero',
    'J9TYAsWWidbrcZybmLSfrLzryANf4CgJBLdvwdGuC8MB': 'Johnson',
    'DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt': 'Kay The Doc',
    'DVMkhiQe1D8yenuEgsW44NjRn9LfVQjGEpZcez5x7Mff': 'Iceman',
}


def paper_only(wallet, traders=None):
    if wallet in PAPER_ONLY_SOURCES:
        return True
    if traders is None:
        from solana_copy_trader import load
        traders = load()[0]['traders']
    return any(r['wallet'] == wallet and r.get('paper_only', False) for r in traders)
