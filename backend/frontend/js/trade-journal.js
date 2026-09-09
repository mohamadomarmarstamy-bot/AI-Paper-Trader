(() => {
    let tradeJournalLoaded = false;

    function getApiUrl() {
        return String(
            window.API_URL ?? ""
        )
            .trim()
            .replace(/\/+$/, "");
    }

    function formatCurrency(value) {
        const number = Number(value);

        if (!Number.isFinite(number)) {
            return "—";
        }

        return number.toLocaleString(
            "en-US",
            {
                style: "currency",
                currency: "USD",
            }
        );
    }

    function formatPercent(value) {
        const number = Number(value);

        if (!Number.isFinite(number)) {
            return "—";
        }

        return `${number.toFixed(2)}%`;
    }

    function formatDateTime(value) {
        if (!value) {
            return "—";
        }

        const date = new Date(value);

        if (Number.isNaN(date.getTime())) {
            return String(value);
        }

        return date.toLocaleString();
    }

    function formatHoldingTime(seconds) {
        const value = Number(seconds);

        if (!Number.isFinite(value) || value < 0) {
            return "—";
        }

        if (value < 60) {
            return `${Math.round(value)} sec`;
        }

        if (value < 3600) {
            return `${Math.round(value / 60)} min`;
        }

        return `${(value / 3600).toFixed(1)} hr`;
    }

    function humanizeReason(value) {
        const text = String(value ?? "")
            .trim();

        if (!text) {
            return "No reason recorded.";
        }

        const knownReasons = {
            auto_trader_entry:
                "The automatic trader opened this position after the scanner and entry filters approved the setup.",

            scanner_exit:
                "The scanner weakened enough to trigger an automatic exit.",

            hard_max_loss_exit:
                "The position reached the hard maximum-loss protection and was exited.",

            defensive_portfolio_exit:
                "Daily profit-giveback protection entered defensive mode and closed this non-profitable position.",

            broker_sell_fill:
                "The position was closed by a broker-side sell or protective order fill.",

            take_profit:
                "The position reached its take-profit protection.",

            stop_loss:
                "The position reached its stop-loss protection.",
        };

        if (knownReasons[text]) {
            return knownReasons[text];
        }

        return text
            .replace(/_/g, " ")
            .replace(/\b\w/g, char =>
                char.toUpperCase()
            );
    }

    function buildBuyExplanation(trade) {
        const diagnostics =
            trade.entry_diagnostics ?? {};

        const learning =
            trade.learning ?? {};

        const parts = [];

        const score =
            learning.entry_score ??
            diagnostics.score;

        const confidence =
            learning.entry_confidence ??
            diagnostics.confidence;

        const signal =
            learning.entry_signal ??
            diagnostics.signal;

        if (signal) {
            parts.push(
                `Scanner signal was ${String(signal).toUpperCase()}.`
            );
        }

        if (score !== null && score !== undefined) {
            parts.push(
                `Entry score was ${Number(score).toFixed(0)}.`
            );
        }

        if (
            confidence !== null &&
            confidence !== undefined
        ) {
            parts.push(
                `Confidence was ${Number(confidence).toFixed(0)}%.`
            );
        }

        if (learning.scanner_rank) {
            parts.push(
                `Scanner rank was #${learning.scanner_rank}.`
            );
        }

        if (diagnostics.trend) {
            parts.push(
                `Trend was ${diagnostics.trend}.`
            );
        }

        if (diagnostics.rsi !== undefined) {
            parts.push(
                `RSI was ${Number(diagnostics.rsi).toFixed(1)}.`
            );
        }

        if (
            diagnostics.volume_ratio !== undefined
        ) {
            parts.push(
                `Volume ratio was ${Number(
                    diagnostics.volume_ratio
                ).toFixed(2)}x.`
            );
        }

        if (!parts.length) {
            return humanizeReason(
                trade.entry_reason
            );
        }

        return parts.join(" ");
    }

    function buildSellExplanation(trade) {
        const learning =
            trade.learning ?? {};

        const parts = [
            humanizeReason(
                learning.exit_reason ??
                trade.exit_reason
            ),
        ];

        if (
            learning.mfe_percent !== null &&
            learning.mfe_percent !== undefined
        ) {
            parts.push(
                `Best excursion after entry was ${formatPercent(
                    learning.mfe_percent
                )}.`
            );
        }

        if (
            learning.mae_percent !== null &&
            learning.mae_percent !== undefined
        ) {
            parts.push(
                `Worst excursion after entry was ${formatPercent(
                    learning.mae_percent
                )}.`
            );
        }

        return parts.join(" ");
    }

    function createMetric(
        label,
        value
    ) {
        const wrapper =
            document.createElement("div");

        wrapper.className =
            "trade-journal-metric";

        const labelElement =
            document.createElement("span");

        labelElement.textContent =
            label;

        const valueElement =
            document.createElement("strong");

        valueElement.textContent =
            value;

        wrapper.append(
            labelElement,
            valueElement
        );

        return wrapper;
    }

    function renderSummary(payload) {
        const container =
            document.getElementById(
                "trade-journal-summary"
            );

        if (!container) {
            return;
        }

        const summary =
            payload?.summary ?? {};

        container.replaceChildren();

        const metrics =
            document.createElement("div");

        metrics.className =
            "trade-journal-summary-grid";

        metrics.append(
            createMetric(
                "Completed Trades",
                String(
                    summary.completed_trades ?? 0
                )
            ),
            createMetric(
                "Wins",
                String(
                    summary.wins ?? 0
                )
            ),
            createMetric(
                "Losses",
                String(
                    summary.losses ?? 0
                )
            ),
            createMetric(
                "Win Rate",
                formatPercent(
                    summary.win_rate_percent
                )
            ),
            createMetric(
                "Realized P/L",
                formatCurrency(
                    summary.total_realized_profit_loss
                )
            ),
            createMetric(
                "Avg Return",
                formatPercent(
                    summary.average_return_percent
                )
            )
        );

        container.appendChild(
            metrics
        );
    }

    function renderTrades(trades) {
        const container =
            document.getElementById(
                "trade-journal-list"
            );

        if (!container) {
            return;
        }

        container.replaceChildren();

        if (!Array.isArray(trades) ||
            !trades.length) {
            const empty =
                document.createElement("p");

            empty.textContent =
                "No completed trades found.";

            container.appendChild(
                empty
            );

            return;
        }

        for (const trade of trades) {
            const card =
                document.createElement(
                    "article"
                );

            card.className =
                "trade-journal-card";

            const header =
                document.createElement(
                    "div"
                );

            header.className =
                "trade-journal-card-header";

            const title =
                document.createElement("h3");

            title.textContent =
                trade.symbol ?? "Unknown";

            const pnl =
                document.createElement(
                    "strong"
                );

            const pnlValue =
                Number(
                    trade.realized_profit_loss
                );

            pnl.textContent =
                formatCurrency(
                    pnlValue
                );

            pnl.className =
                "trade-journal-pnl";

            if (pnlValue > 0) {
                pnl.classList.add(
                    "positive"
                );
            } else if (pnlValue < 0) {
                pnl.classList.add(
                    "negative"
                );
            }

            header.append(
                title,
                pnl
            );

            const metrics =
                document.createElement(
                    "div"
                );

            metrics.className =
                "trade-journal-metrics-grid";

            metrics.append(
                createMetric(
                    "Shares",
                    String(
                        trade.shares ?? "—"
                    )
                ),
                createMetric(
                    "Buy",
                    formatCurrency(
                        trade.entry_price
                    )
                ),
                createMetric(
                    "Sell",
                    formatCurrency(
                        trade.exit_price
                    )
                ),
                createMetric(
                    "Return",
                    formatPercent(
                        trade.realized_return_percent
                    )
                ),
                createMetric(
                    "Held",
                    formatHoldingTime(
                        trade.learning
                            ?.holding_seconds
                    )
                ),
                createMetric(
                    "MFE",
                    formatPercent(
                        trade.learning
                            ?.mfe_percent
                    )
                ),
                createMetric(
                    "MAE",
                    formatPercent(
                        trade.learning
                            ?.mae_percent
                    )
                )
            );

            const details =
                document.createElement(
                    "details"
                );

            details.className =
                "trade-journal-details";

            const summary =
                document.createElement(
                    "summary"
                );

            summary.textContent =
                "View AI Explanation";

            const explanation =
                document.createElement(
                    "div"
                );

            explanation.className =
                "trade-journal-explanation";

            const buyHeading =
                document.createElement("h4");

            buyHeading.textContent =
                "Why the AI bought";

            const buyText =
                document.createElement("p");

            buyText.textContent =
                buildBuyExplanation(
                    trade
                );

            const sellHeading =
                document.createElement("h4");

            sellHeading.textContent =
                "Why the AI sold";

            const sellText =
                document.createElement("p");

            sellText.textContent =
                buildSellExplanation(
                    trade
                );

            const timing =
                document.createElement("p");

            timing.className =
                "trade-journal-timestamps";

            timing.textContent =
                `Bought: ${formatDateTime(
                    trade.entry_timestamp
                )} | Sold: ${formatDateTime(
                    trade.exit_timestamp
                )}`;

            explanation.append(
                buyHeading,
                buyText,
                sellHeading,
                sellText,
                timing
            );

            details.append(
                summary,
                explanation
            );

            card.append(
                header,
                metrics,
                details
            );

            container.appendChild(
                card
            );
        }
    }

    async function loadTradeJournal(
        {
            force = false,
        } = {}
    ) {
        if (
            tradeJournalLoaded &&
            !force
        ) {
            return;
        }

        const apiUrl =
            getApiUrl();

        const summaryContainer =
            document.getElementById(
                "trade-journal-summary"
            );

        const listContainer =
            document.getElementById(
                "trade-journal-list"
            );

        if (!apiUrl) {
            return;
        }

        if (summaryContainer) {
            summaryContainer.textContent =
                "Loading trade summary…";
        }

        if (listContainer) {
            listContainer.textContent =
                "Loading completed trades…";
        }

        try {
            const response =
                await fetch(
                    `${apiUrl}/auto-trader/history?limit=500`,
                    {
                        credentials:
                            "include",
                    }
                );

            if (!response.ok) {
                throw new Error(
                    `History request failed (${response.status}).`
                );
            }

            const payload =
                await response.json();

            renderSummary(
                payload
            );

            renderTrades(
                payload?.trades ?? []
            );

            tradeJournalLoaded =
                true;

        } catch (error) {
            const message =
                error instanceof Error
                    ? error.message
                    : "Could not load trade journal.";

            if (summaryContainer) {
                summaryContainer.textContent =
                    message;
            }

            if (listContainer) {
                listContainer.textContent =
                    message;
            }
        }
    }

    document
        .getElementById(
            "refresh-trade-journal-button"
        )
        ?.addEventListener(
            "click",
            () =>
                loadTradeJournal({
                    force: true,
                })
        );

    window.loadTradeJournal =
        loadTradeJournal;
})();
