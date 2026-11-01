"""Owner-selected paper-only sources. Applies to new live buys, never exits."""
from solana_paper_trials import CANDIDATES

PAPER_ONLY_SOURCES = {
    **{wallet: name for name, wallet in CANDIDATES},
    'ApRnQN2HkbCn7W2WWiT2FEKvuKJp9LugRyAE1a9Hdz1': 'Solkrow',
    'DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt': 'Kay The Doc',
}


def paper_only(wallet):
    return wallet in PAPER_ONLY_SOURCES
