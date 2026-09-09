(() => {
    let taxDataLoaded = false;
    let cachedTrades = [];

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

    function formatDate(value) {
        if (!value) {
            return "—";
        }

        const date = new Date(value);

        if (Number.isNaN(date.getTime())) {
            return String(value);
        }

        return date.toLocaleDateString();
    }

    function getTradeYear(trade) {
        const timestamp =
            trade?.exit_timestamp;

        if (!timestamp) {
            return null;
        }

        const date = new Date(timestamp);

        if (Number.isNaN(date.getTime())) {
            return null;
        }

        return date.getFullYear();
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

    function populateTaxYears(trades) {
        const select =
            document.getElementById(
                "tax-year"
            );

        if (!select) {
            return;
        }

        const years = Array.from(
            new Set(
                trades
                    .map(getTradeYear)
                    .filter(year =>
                        Number.isInteger(year)
                    )
            )
        ).sort(
            (a, b) => b - a
        );

        const currentValue =
            Number(select.value);

        select.replaceChildren();

        const availableYears =
            years.length
                ? years
                : [new Date().getFullYear()];

        for (const year of availableYears) {
            const option =
                document.createElement(
                    "option"
                );

            option.value =
                String(year);

            option.textContent =
                String(year);

            select.appendChild(
                option
            );
        }

        if (
            availableYears.includes(
                currentValue
            )
        ) {
            select.value =
                String(currentValue);
        }
    }

    function getTradesForSelectedYear() {
        const select =
            document.getElementById(
                "tax-year"
            );

        const year =
            Number(select?.value);

        if (!Number.isInteger(year)) {
            return [];
        }

        return cachedTrades.filter(
            trade =>
                getTradeYear(trade) ===
                year
        );
    }

    function renderTaxSummary(trades) {
        const container =
            document.getElementById(
                "tax-summary"
            );

        if (!container) {
            return;
        }

        container.replaceChildren();

        let gains = 0;
        let losses = 0;
        let net = 0;
        let proceeds = 0;
        let costBasis = 0;

        for (const trade of trades) {
            const pnl =
                Number(
                    trade.realized_profit_loss
                );

            const shares =
                Number(trade.shares);

            const entry =
                Number(trade.entry_price);

            const exit =
                Number(trade.exit_price);

            if (Number.isFinite(pnl)) {
                net += pnl;

                if (pnl > 0) {
                    gains += pnl;
                } else if (pnl < 0) {
                    losses += pnl;
                }
            }

            if (
                Number.isFinite(shares) &&
                Number.isFinite(entry)
            ) {
                costBasis +=
                    shares * entry;
            }

            if (
                Number.isFinite(shares) &&
                Number.isFinite(exit)
            ) {
                proceeds +=
                    shares * exit;
            }
        }

        const metrics =
            document.createElement("div");

        metrics.className =
            "tax-summary-grid";

        metrics.append(
            createMetric(
                "Closed Trades",
                String(trades.length)
            ),
            createMetric(
                "Realized Gains",
                formatCurrency(gains)
            ),
            createMetric(
                "Realized Losses",
                formatCurrency(losses)
            ),
            createMetric(
                "Net Realized P/L",
                formatCurrency(net)
            ),
            createMetric(
                "Estimated Proceeds",
                formatCurrency(proceeds)
            ),
            createMetric(
                "Estimated Cost Basis",
                formatCurrency(costBasis)
            )
        );

        const note =
            document.createElement("p");

        note.className =
            "trade-journal-timestamps";

        note.textContent =
            "Paper-trading estimate only. This is not an official broker tax form and does not account for tax rules such as wash sales or other adjustments.";

        container.append(
            metrics,
            note
        );
    }

    function renderTaxTrades(trades) {
        const container =
            document.getElementById(
                "tax-trades-list"
            );

        if (!container) {
            return;
        }

        container.replaceChildren();

        if (!trades.length) {
            const empty =
                document.createElement("p");

            empty.textContent =
                "No closed trades found for this tax year.";

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
                formatCurrency(pnlValue);

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
                    "Exit Date",
                    formatDate(
                        trade.exit_timestamp
                    )
                ),
                createMetric(
                    "Shares",
                    String(
                        trade.shares ?? "—"
                    )
                ),
                createMetric(
                    "Entry",
                    formatCurrency(
                        trade.entry_price
                    )
                ),
                createMetric(
                    "Exit",
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
                    "P/L",
                    formatCurrency(
                        trade.realized_profit_loss
                    )
                )
            );

            card.append(
                header,
                metrics
            );

            container.appendChild(
                card
            );
        }
    }

    function renderSelectedTaxYear() {
        const trades =
            getTradesForSelectedYear();

        renderTaxSummary(
            trades
        );

        renderTaxTrades(
            trades
        );
    }

    async function loadTaxes(
        {
            force = false,
        } = {}
    ) {
        if (
            taxDataLoaded &&
            !force
        ) {
            renderSelectedTaxYear();
            return;
        }

        const apiUrl =
            getApiUrl();

        const summary =
            document.getElementById(
                "tax-summary"
            );

        const list =
            document.getElementById(
                "tax-trades-list"
            );

        if (!apiUrl) {
            return;
        }

        if (summary) {
            summary.textContent =
                "Loading tax summary…";
        }

        if (list) {
            list.textContent =
                "Loading closed trades…";
        }

        try {
            const response =
                await fetch(
                    `${apiUrl}/auto-trader/history?limit=5000`,
                    {
                        credentials:
                            "include",
                    }
                );

            if (!response.ok) {
                throw new Error(
                    `Tax history request failed (${response.status}).`
                );
            }

            const payload =
                await response.json();

            cachedTrades =
                Array.isArray(
                    payload?.trades
                )
                    ? payload.trades
                    : [];

            populateTaxYears(
                cachedTrades
            );

            renderSelectedTaxYear();

            taxDataLoaded =
                true;

        } catch (error) {
            const message =
                error instanceof Error
                    ? error.message
                    : "Could not load tax data.";

            if (summary) {
                summary.textContent =
                    message;
            }

            if (list) {
                list.textContent =
                    message;
            }
        }
    }

    document
        .getElementById(
            "tax-year"
        )
        ?.addEventListener(
            "change",
            renderSelectedTaxYear
        );

    window.loadTaxes =
        loadTaxes;
})();
