"""Track SQLite connections in temporary PAPER-only tests."""

from contextlib import contextmanager
from unittest.mock import patch

import database
import pro_ticker_budget_ledger
import pro_ticker_exit_links
import pro_ticker_manual_intake


@contextmanager
def tracked_test_connections():
    connections = []
    original = database.get_connection

    def connect():
        conn = original()
        connections.append(conn)
        return conn

    modules = (
        database,
        pro_ticker_budget_ledger,
        pro_ticker_exit_links,
        pro_ticker_manual_intake,
    )

    with (
        patch.object(database, "get_connection", side_effect=connect),
        patch.object(pro_ticker_budget_ledger, "get_connection", side_effect=connect),
        patch.object(pro_ticker_exit_links, "get_connection", side_effect=connect),
        patch.object(pro_ticker_manual_intake, "get_connection", side_effect=connect),
    ):
        try:
            yield
        finally:
            for conn in reversed(connections):
                conn.close()
