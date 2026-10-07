from unittest.mock import patch

import main


def make_position():
    return {
        "symbol": "TEST",
        "qty": "10",
        "current_price": "100.00",
    }


def make_protection():
    return {
        "id": "protect-order-123",
        "symbol": "TEST",
        "qty": "10",
        "side": "sell",
        "type": "stop",
        "stop_price": "98.00",
        "client_order_id": "auto-protect-test-123",
    }


def make_fresh_quote():
    return {
        "bid": 100.00,
        "spread_percent": 0.25,
        "timestamp": "2099-01-01T00:00:00+00:00",
    }


def test_unknown_manual_sell_is_blocked():
    position = make_position()

    manual_sell = {
        "id": "manual-order-123",
        "symbol": "TEST",
        "qty": "10",
        "side": "sell",
        "type": "stop",
        "stop_price": "98.00",
        "client_order_id": "manual-sell-order",
    }

    with (
        patch.object(
            main,
            "fetch_alpaca_paper_positions",
            return_value=[position],
        ),
        patch.object(
            main,
            "fetch_alpaca_open_orders_for_symbol",
            return_value=[manual_sell],
        ),
        patch.object(
            main,
            "cancel_alpaca_open_orders_for_symbol",
        ) as cancel_mock,
        patch.object(
            main,
            "fetch_alpaca_risk_quote",
        ) as quote_mock,
        patch.object(
            main,
            "submit_alpaca_paper_extended_hours_exit",
        ) as exit_mock,
    ):
        result = main.safely_submit_extended_hours_exit(
            symbol="TEST",
            reason="extended_hours_profit_trail",
        )

    assert result["success"] is False
    assert result["blocked_unrecognized_order"] is True

    cancel_mock.assert_not_called()
    quote_mock.assert_not_called()
    exit_mock.assert_not_called()

    print("PASS: unknown/manual SELL was blocked")
    print("PASS: no orders were canceled")
    print("PASS: no extended-hours SELL was submitted")


def test_app_owned_protection_can_proceed():
    position = make_position()
    protection = make_protection()

    fake_exit_result = {
        "success": True,
        "paper": True,
        "symbol": "TEST",
        "order_id": "fake-ext-order",
    }

    with (
        patch.object(
            main,
            "fetch_alpaca_paper_positions",
            side_effect=[
                [position],
                [position],
            ],
        ),
        patch.object(
            main,
            "fetch_alpaca_open_orders_for_symbol",
            return_value=[protection],
        ),
        patch.object(
            main,
            "get_open_protective_stop_order",
            return_value=protection,
        ),
        patch.object(
            main,
            "fetch_alpaca_risk_quote",
            return_value=make_fresh_quote(),
        ),
        patch.object(
            main,
            "cancel_alpaca_open_orders_for_symbol",
            return_value=["protect-order-123"],
        ) as cancel_mock,
        patch.object(
            main,
            "submit_alpaca_paper_extended_hours_exit",
            return_value=fake_exit_result,
        ) as exit_mock,
        patch.object(
            main,
            "datetime",
            wraps=main.datetime,
        ) as datetime_mock,
    ):
        datetime_mock.now.return_value = (
            main.datetime.fromisoformat(
                "2099-01-01T00:00:30+00:00"
            )
        )

        result = main.safely_submit_extended_hours_exit(
            symbol="TEST",
            reason="extended_hours_profit_trail",
        )

    assert result["success"] is True
    assert result["canceled_orders"] == [
        "protect-order-123"
    ]

    cancel_mock.assert_called_once_with("TEST")

    exit_mock.assert_called_once_with(
        symbol="TEST",
        shares=10,
        reason="extended_hours_profit_trail",
    )

    print("PASS: app-owned protection was recognized")
    print("PASS: approved protection reached cancellation path")
    print("PASS: mocked extended-hours SELL was submitted")


def test_failed_exit_restores_protection():
    position = make_position()
    protection = make_protection()

    failed_exit = {
        "success": False,
        "paper": True,
        "symbol": "TEST",
        "error": "Simulated extended-hours order failure",
    }

    recovery_result = {
        "success": True,
        "paper": True,
        "symbol": "TEST",
        "recovered": True,
    }

    with (
        patch.object(
            main,
            "fetch_alpaca_paper_positions",
            side_effect=[
                [position],
                [position],
            ],
        ),
        patch.object(
            main,
            "fetch_alpaca_open_orders_for_symbol",
            return_value=[protection],
        ),
        patch.object(
            main,
            "get_open_protective_stop_order",
            return_value=protection,
        ),
        patch.object(
            main,
            "fetch_alpaca_risk_quote",
            return_value=make_fresh_quote(),
        ),
        patch.object(
            main,
            "cancel_alpaca_open_orders_for_symbol",
            return_value=["protect-order-123"],
        ) as cancel_mock,
        patch.object(
            main,
            "submit_alpaca_paper_extended_hours_exit",
            return_value=failed_exit,
        ) as exit_mock,
        patch.object(
            main,
            "recover_extended_hours_protection_after_exception",
            return_value=recovery_result,
        ) as recovery_mock,
        patch.object(
            main,
            "datetime",
            wraps=main.datetime,
        ) as datetime_mock,
    ):
        datetime_mock.now.return_value = (
            main.datetime.fromisoformat(
                "2099-01-01T00:00:30+00:00"
            )
        )

        result = main.safely_submit_extended_hours_exit(
            symbol="TEST",
            reason="extended_hours_profit_trail",
        )

    assert result["success"] is False

    cancel_mock.assert_called_once_with("TEST")

    exit_mock.assert_called_once_with(
        symbol="TEST",
        shares=10,
        reason="extended_hours_profit_trail",
    )

    recovery_mock.assert_called_once_with(
        symbol="TEST",
    )

    assert (
        result.get("protection_recovery")
        == recovery_result
    )

    print("PASS: simulated extended-hours SELL failure detected")
    print("PASS: recovery protection was attempted after cancellation")
    print("PASS: recovery result was preserved in wrapper response")


def test_empty_cancel_result_is_verified():
    position = make_position()
    protection = make_protection()

    fake_exit_result = {
        "success": True,
        "paper": True,
        "symbol": "TEST",
        "order_id": "fake-ext-order",
    }

    with (
        patch.object(
            main,
            "fetch_alpaca_paper_positions",
            side_effect=[
                [position],
                [position],
            ],
        ),
        patch.object(
            main,
            "fetch_alpaca_open_orders_for_symbol",
            side_effect=[
                [protection],
                [],
            ],
        ) as open_orders_mock,
        patch.object(
            main,
            "get_open_protective_stop_order",
            return_value=protection,
        ),
        patch.object(
            main,
            "fetch_alpaca_risk_quote",
            return_value=make_fresh_quote(),
        ),
        patch.object(
            main,
            "cancel_alpaca_open_orders_for_symbol",
            return_value=[],
        ) as cancel_mock,
        patch.object(
            main,
            "submit_alpaca_paper_extended_hours_exit",
            return_value=fake_exit_result,
        ) as exit_mock,
        patch.object(
            main,
            "recover_extended_hours_protection_after_exception",
        ) as recovery_mock,
        patch.object(
            main,
            "datetime",
            wraps=main.datetime,
        ) as datetime_mock,
    ):
        datetime_mock.now.return_value = (
            main.datetime.fromisoformat(
                "2099-01-01T00:00:30+00:00"
            )
        )

        result = main.safely_submit_extended_hours_exit(
            symbol="TEST",
            reason="extended_hours_profit_trail",
        )

    assert result["success"] is True

    assert result["canceled_orders"] == [
        "protect-order-123"
    ]

    cancel_mock.assert_called_once_with("TEST")
    assert open_orders_mock.call_count == 2

    exit_mock.assert_called_once_with(
        symbol="TEST",
        shares=10,
        reason="extended_hours_profit_trail",
    )

    recovery_mock.assert_not_called()

    print("PASS: empty cancellation result triggered broker verification")
    print("PASS: broker confirmed protection was actually removed")
    print("PASS: guarded extended-hours exit continued safely")


def test_empty_cancel_with_protection_remaining_stops():
    position = make_position()
    protection = make_protection()

    with (
        patch.object(
            main,
            "fetch_alpaca_paper_positions",
            return_value=[position],
        ),
        patch.object(
            main,
            "fetch_alpaca_open_orders_for_symbol",
            side_effect=[
                [protection],
                [protection],
            ],
        ) as open_orders_mock,
        patch.object(
            main,
            "get_open_protective_stop_order",
            return_value=protection,
        ),
        patch.object(
            main,
            "fetch_alpaca_risk_quote",
            return_value=make_fresh_quote(),
        ),
        patch.object(
            main,
            "cancel_alpaca_open_orders_for_symbol",
            return_value=[],
        ) as cancel_mock,
        patch.object(
            main,
            "submit_alpaca_paper_extended_hours_exit",
        ) as exit_mock,
        patch.object(
            main,
            "recover_extended_hours_protection_after_exception",
        ) as recovery_mock,
        patch.object(
            main,
            "datetime",
            wraps=main.datetime,
        ) as datetime_mock,
    ):
        datetime_mock.now.return_value = (
            main.datetime.fromisoformat(
                "2099-01-01T00:00:30+00:00"
            )
        )

        result = main.safely_submit_extended_hours_exit(
            symbol="TEST",
            reason="extended_hours_profit_trail",
        )

    assert result["success"] is False
    assert result["orders_still_open"] is True

    cancel_mock.assert_called_once_with("TEST")
    assert open_orders_mock.call_count == 2

    exit_mock.assert_not_called()
    recovery_mock.assert_not_called()

    print("PASS: failed cancellation triggered broker verification")
    print("PASS: broker confirmed protection was still open")
    print("PASS: duplicate extended-hours SELL was blocked")




def test_cancel_verification_error_attempts_recovery():
    position = make_position()
    protection = make_protection()

    recovery_result = {
        "success": True,
        "paper": True,
        "symbol": "TEST",
        "recovered": True,
    }

    # First lookup sees protection.
    # Second lookup simulates the broker failing while
    # verifying what happened after cancellation.
    with (
        patch.object(
            main,
            "fetch_alpaca_paper_positions",
            return_value=[position],
        ),
        patch.object(
            main,
            "fetch_alpaca_open_orders_for_symbol",
            side_effect=[
                [protection],
                RuntimeError(
                    "Simulated broker verification failure"
                ),
            ],
        ) as open_orders_mock,
        patch.object(
            main,
            "get_open_protective_stop_order",
            return_value=protection,
        ),
        patch.object(
            main,
            "fetch_alpaca_risk_quote",
            return_value=make_fresh_quote(),
        ),
        patch.object(
            main,
            "cancel_alpaca_open_orders_for_symbol",
            return_value=[],
        ) as cancel_mock,
        patch.object(
            main,
            "submit_alpaca_paper_extended_hours_exit",
        ) as exit_mock,
        patch.object(
            main,
            "recover_extended_hours_protection_after_exception",
            return_value=recovery_result,
        ) as recovery_mock,
        patch.object(
            main,
            "datetime",
            wraps=main.datetime,
        ) as datetime_mock,
    ):
        datetime_mock.now.return_value = (
            main.datetime.fromisoformat(
                "2099-01-01T00:00:30+00:00"
            )
        )

        result = main.safely_submit_extended_hours_exit(
            symbol="TEST",
            reason="extended_hours_profit_trail",
        )

    assert result["success"] is False

    assert (
        result.get("protection_recovery")
        == recovery_result
    )

    cancel_mock.assert_called_once_with("TEST")

    assert open_orders_mock.call_count == 2

    recovery_mock.assert_called_once_with(
        symbol="TEST",
    )

    exit_mock.assert_not_called()

    print(
        "PASS: cancellation verification failure "
        "was treated as ambiguous"
    )
    print(
        "PASS: recovery protection was attempted "
        "instead of guessing"
    )
    print(
        "PASS: extended-hours SELL stayed blocked "
        "during verification failure"
    )


if __name__ == "__main__":
    test_unknown_manual_sell_is_blocked()
    test_app_owned_protection_can_proceed()
    test_failed_exit_restores_protection()
    test_empty_cancel_result_is_verified()
    test_empty_cancel_with_protection_remaining_stops()
    test_cancel_verification_error_attempts_recovery()