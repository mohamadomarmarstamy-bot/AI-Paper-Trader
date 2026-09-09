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

    function getTaxDayKey(trade) {
        const timestamp =
            trade?.exit_timestamp;

        if (!timestamp) {
            return "unknown";
        }

        const date = new Date(timestamp);

        if (Number.isNaN(date.getTime())) {
            return "unknown";
        }

        const year =
            date.getFullYear();

        const month =
            String(
                date.getMonth() + 1
            ).padStart(2, "0");

        const day =
            String(
                date.getDate()
            ).padStart(2, "0");

        return `${year}-${month}-${day}`;
    }


    function formatTaxDayLabel(trade) {
        const timestamp =
            trade?.exit_timestamp;

        if (!timestamp) {
            return "Unknown Date";
        }

        const date =
            new Date(timestamp);

        if (Number.isNaN(date.getTime())) {
            return "Unknown Date";
        }

        return date.toLocaleDateString(
            "en-US",
            {
                month: "long",
                day: "numeric",
                year: "numeric",
            }
        );
    }


    function groupTaxTradesByDay(trades) {
        const grouped =
            new Map();

        for (const trade of trades) {
            const key =
                getTaxDayKey(trade);

            if (!grouped.has(key)) {
                grouped.set(
                    key,
                    []
                );
            }

            grouped
                .get(key)
                .push(trade);
        }

        return Array.from(
            grouped.entries()
        ).sort(
            ([left], [right]) =>
                String(right)
                    .localeCompare(
                        String(left)
                    )
        );
    }


    function createTaxTradeRow(trade) {
        const details =
            document.createElement(
                "details"
            );

        details.className =
            "trade-journal-trade-row";

        const summary =
            document.createElement(
                "summary"
            );

        summary.className =
            "trade-journal-trade-summary";

        const symbol =
            document.createElement(
                "strong"
            );

        symbol.className =
            "trade-journal-row-symbol";

        symbol.textContent =
            trade.symbol ??
            "Unknown";

        const shares =
            document.createElement(
                "span"
            );

        shares.textContent =
            `${trade.shares ?? "-"} sh`;

        const prices =
            document.createElement(
                "span"
            );

        prices.textContent =
            `${formatCurrency(
                trade.entry_price
            )} -> ${formatCurrency(
                trade.exit_price
            )}`;

        const pnl =
            document.createElement(
                "strong"
            );

        const pnlValue =
            Number(
                trade.realized_profit_loss
            );

        pnl.className =
            "trade-journal-pnl";

        pnl.textContent =
            formatCurrency(
                pnlValue
            );

        if (pnlValue > 0) {
            pnl.classList.add(
                "positive"
            );
        } else if (pnlValue < 0) {
            pnl.classList.add(
                "negative"
            );
        }

        const returnValue =
            document.createElement(
                "span"
            );

        returnValue.textContent =
            formatPercent(
                trade.realized_return_percent
            );

        summary.append(
            symbol,
            shares,
            prices,
            pnl,
            returnValue
        );

        const expanded =
            document.createElement(
                "div"
            );

        expanded.className =
            "trade-journal-trade-expanded";

        const entryValue =
            Number(trade.entry_price);

        const exitValue =
            Number(trade.exit_price);

        const shareValue =
            Number(trade.shares);

        const costBasis =
            Number.isFinite(entryValue) &&
            Number.isFinite(shareValue)
                ? entryValue * shareValue
                : null;

        const proceeds =
            Number.isFinite(exitValue) &&
            Number.isFinite(shareValue)
                ? exitValue * shareValue
                : null;

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
                "Cost Basis",
                formatCurrency(
                    costBasis
                )
            ),
            createMetric(
                "Proceeds",
                formatCurrency(
                    proceeds
                )
            ),
            createMetric(
                "Realized P/L",
                formatCurrency(
                    trade.realized_profit_loss
                )
            ),
            createMetric(
                "Return",
                formatPercent(
                    trade.realized_return_percent
                )
            )
        );

        const note =
            document.createElement(
                "p"
            );

        note.className =
            "trade-journal-timestamps";

        note.textContent =
            "Paper-trading estimate only. "
            + "Not an official broker tax record.";

        expanded.append(
            metrics,
            note
        );

        details.append(
            summary,
            expanded
        );

        return details;
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
                document.createElement(
                    "p"
                );

            empty.textContent =
                "No closed trades found for this tax year.";

            container.appendChild(
                empty
            );

            return;
        }

        const groupedDays =
            groupTaxTradesByDay(
                trades
            );

        for (
            const [
                dayKey,
                dayTrades,
            ]
            of groupedDays
        ) {
            const dayDetails =
                document.createElement(
                    "details"
                );

            dayDetails.className =
                "trade-journal-day";

            dayDetails.dataset.day =
                dayKey;

            const daySummary =
                document.createElement(
                    "summary"
                );

            daySummary.className =
                "trade-journal-day-summary";

            const dayLabel =
                document.createElement(
                    "strong"
                );

            dayLabel.className =
                "trade-journal-day-date";

            dayLabel.textContent =
                formatTaxDayLabel(
                    dayTrades[0]
                );

            const dayPnl =
                dayTrades.reduce(
                    (total, trade) => {
                        const value =
                            Number(
                                trade.realized_profit_loss
                            );

                        return total + (
                            Number.isFinite(value)
                                ? value
                                : 0
                        );
                    },
                    0
                );

            const pnl =
                document.createElement(
                    "strong"
                );

            pnl.className =
                "trade-journal-pnl";

            pnl.textContent =
                formatCurrency(
                    dayPnl
                );

            if (dayPnl > 0) {
                pnl.classList.add(
                    "positive"
                );
            } else if (dayPnl < 0) {
                pnl.classList.add(
                    "negative"
                );
            }

            const count =
                document.createElement(
                    "span"
                );

            count.textContent =
                `${dayTrades.length} trade${
                    dayTrades.length === 1
                        ? ""
                        : "s"
                }`;

            const hint =
                document.createElement(
                    "span"
                );

            hint.className =
                "trade-journal-day-hint";

            hint.textContent =
                "Click to expand";

            const mainLine =
                document.createElement(
                    "span"
                );

            mainLine.className =
                "trade-journal-day-main";

            mainLine.append(
                dayLabel,
                pnl,
                count
            );

            daySummary.append(
                mainLine,
                hint
            );

            dayDetails.addEventListener(
                "toggle",
                () => {
                    hint.textContent =
                        dayDetails.open
                            ? "Click to unexpand"
                            : "Click to expand";
                }
            );

            const body =
                document.createElement(
                    "div"
                );

            body.className =
                "trade-journal-day-body";

            for (
                const trade
                of dayTrades
            ) {
                body.appendChild(
                    createTaxTradeRow(
                        trade
                    )
                );
            }

            dayDetails.append(
                daySummary,
                body
            );

            container.appendChild(
                dayDetails
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
