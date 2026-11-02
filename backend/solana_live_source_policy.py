"""Owner-selected paper-only sources. Applies to new live buys, never exits."""
from solana_paper_trials import CANDIDATES

PAPER_ONLY_SOURCES = {
    **{wallet: name for name, wallet in CANDIDATES},
    'GJA1HEbxGnqBhBifH9uQauzXSB53to5rhDrzmKxhSU65': 'Latuche',
}


def paper_only(wallet):
    return wallet in PAPER_ONLY_SOURCES
