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

    function getTradeDayKey(trade) {
        const value =
            trade?.exit_timestamp ??
            trade?.entry_timestamp;

        if (!value) {
            return "unknown";
        }

        const date = new Date(value);

        if (Number.isNaN(date.getTime())) {
            return "unknown";
        }

        const year = date.getFullYear();

        const month = String(
            date.getMonth() + 1
        ).padStart(2, "0");

        const day = String(
            date.getDate()
        ).padStart(2, "0");

        return `${year}-${month}-${day}`;
    }


    function formatTradeDayLabel(trade) {
        const value =
            trade?.exit_timestamp ??
            trade?.entry_timestamp;

        if (!value) {
            return "Unknown Date";
        }

        const date = new Date(value);

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


    function groupTradesByDay(trades) {
        const grouped = new Map();

        for (const trade of trades) {
            const key =
                getTradeDayKey(trade);

            if (!grouped.has(key)) {
                grouped.set(key, []);
            }

            grouped
                .get(key)
                .push(trade);
        }

        return Array.from(
            grouped.entries()
        ).sort(
            ([left], [right]) =>
                String(right).localeCompare(
                    String(left)
                )
        );
    }


    function createTradeRow(trade) {
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
            trade.symbol ?? "Unknown";

        const shares =
            document.createElement("span");

        shares.textContent =
            `${trade.shares ?? "-"} sh`;

        const prices =
            document.createElement("span");

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
            formatCurrency(pnlValue);

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
            document.createElement("span");

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
            document.createElement("div");

        expanded.className =
            "trade-journal-trade-expanded";

        const learning =
            trade.learning ?? {};

        const metrics =
            document.createElement("div");

        metrics.className =
            "trade-journal-metrics-grid";

        metrics.append(
            createMetric(
                "Held",
                formatHoldingTime(
                    learning.holding_seconds
                )
            ),
            createMetric(
                "Score",
                learning.entry_score ??
                trade.entry_diagnostics?.score ??
                "-"
            ),
            createMetric(
                "Confidence",
                learning.entry_confidence !== null &&
                learning.entry_confidence !== undefined
                    ? `${learning.entry_confidence}%`
                    : "-"
            ),
            createMetric(
                "Scanner Rank",
                learning.scanner_rank
                    ? `#${learning.scanner_rank}`
                    : "-"
            ),
            createMetric(
                "MFE",
                formatPercent(
                    learning.mfe_percent
                )
            ),
            createMetric(
                "MAE",
                formatPercent(
                    learning.mae_percent
                )
            )
        );

        const explanation =
            document.createElement("div");

        explanation.className =
            "trade-journal-explanation";

        const buyHeading =
            document.createElement("h4");

        buyHeading.textContent =
            "Why the AI bought";

        const buyText =
            document.createElement("p");

        buyText.textContent =
            buildBuyExplanation(trade);

        const sellHeading =
            document.createElement("h4");

        sellHeading.textContent =
            "Why the AI sold";

        const sellText =
            document.createElement("p");

        sellText.textContent =
            buildSellExplanation(trade);

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

        expanded.append(
            metrics,
            explanation
        );

        details.append(
            summary,
            expanded
        );

        return details;
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

        if (
            !Array.isArray(trades) ||
            !trades.length
        ) {
            const empty =
                document.createElement("p");

            empty.textContent =
                "No completed trades found.";

            container.appendChild(empty);

            return;
        }

        const groupedDays =
            groupTradesByDay(trades);

        for (
            const [dayKey, dayTrades]
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
                formatTradeDayLabel(
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
                formatCurrency(dayPnl);

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

            const pdfButton =
                document.createElement(
                    "button"
                );

            pdfButton.type =
                "button";

            pdfButton.className =
                "secondary-button trade-journal-pdf-button";

            pdfButton.textContent =
                "Daily PDF";

            pdfButton.title =
                "Download daily trade report";

            pdfButton.addEventListener(
                "click",
                (event) => {
                    event.preventDefault();
                    event.stopPropagation();

                    if (
                        !dayKey ||
                        dayKey === "unknown"
                    ) {
                        return;
                    }

                    downloadTradeReport(
                        "/auto-trader/report/daily.pdf"
                        + "?date="
                        + encodeURIComponent(
                            dayKey
                        ),
                        `ai-paper-trader-${dayKey}.pdf`
                    );
                }
            );

            const clickHint =
                document.createElement(
                    "span"
                );

            clickHint.className =
                "trade-journal-day-hint";

            clickHint.textContent =
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
                count,
                pdfButton
            );

            daySummary.append(
                mainLine,
                clickHint
            );

            const dayBody =
                document.createElement(
                    "div"
                );

            dayBody.className =
                "trade-journal-day-body";

            for (const trade of dayTrades) {
                dayBody.appendChild(
                    createTradeRow(trade)
                );
            }

            dayDetails.append(
                daySummary,
                dayBody
            );

            dayDetails.addEventListener(
                "toggle",
                () => {
                    clickHint.textContent =
                        dayDetails.open
                            ? "Click to unexpand"
                            : "Click to expand";
                }
            );


            container.appendChild(
                dayDetails
            );
        }
    }


    async function downloadTradeReport(
        url,
        fallbackFilename
    ) {
        try {
            const response = await fetch(
                url,
                {
                    credentials: "include",
                }
            );

            if (!response.ok) {
                let message =
                    `Report download failed (${response.status}).`;

                try {
                    const payload =
                        await response.json();

                    message =
                        payload?.detail ??
                        payload?.message ??
                        message;
                } catch {
                    // Keep fallback message.
                }

                throw new Error(message);
            }

            const blob =
                await response.blob();

            const disposition =
                response.headers.get(
                    "Content-Disposition"
                ) ?? "";

            const match =
                disposition.match(
                    /filename="?([^"]+)"?/i
                );

            const filename =
                match?.[1] ??
                fallbackFilename;

            const objectUrl =
                URL.createObjectURL(blob);

            const link =
                document.createElement("a");

            link.href =
                objectUrl;

            link.download =
                filename;

            document.body.appendChild(
                link
            );

            link.click();
            link.remove();

            window.setTimeout(
                () => URL.revokeObjectURL(
                    objectUrl
                ),
                1000
            );

        } catch (error) {
            const message =
                error instanceof Error
                    ? error.message
                    : "Could not download report.";

            window.alert(message);
        }
    }


    function initializeTradeJournalReports() {
        const monthInput =
            document.getElementById(
                "trade-journal-month"
            );

        const monthlyButton =
            document.getElementById(
                "trade-journal-monthly-pdf-button"
            );

        if (
            !monthInput ||
            !monthlyButton
        ) {
            return;
        }

        if (!monthInput.value) {
            const now = new Date();

            const year =
                now.getFullYear();

            const month =
                String(
                    now.getMonth() + 1
                ).padStart(2, "0");

            monthInput.value =
                `${year}-${month}`;
        }

        if (
            monthlyButton.dataset
                .reportInitialized === "true"
        ) {
            return;
        }

        monthlyButton.dataset
            .reportInitialized = "true";

        monthlyButton.addEventListener(
            "click",
            () => {
                const month =
                    monthInput.value;

                if (!month) {
                    return;
                }

                downloadTradeReport(
                    "/auto-trader/report/monthly.pdf"
                    + "?month="
                    + encodeURIComponent(
                        month
                    ),
                    `ai-paper-trader-${month}.pdf`
                );
            }
        );
    }


    async function loadTradeJournal(
        {
            force = false,
        } = {}
    ) {
        initializeTradeJournalReports();
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
