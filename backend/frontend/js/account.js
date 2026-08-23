/* account.js
 * Account dashboard rendering and interactions.
 * Depends on:
 *   - window.API_URL
 *   - Chart.js (optional; the page still works without the chart)
 */

"use strict";

let allocationChart = null;
let accountRequestController = null;

let liveAccountRequestController = null;
let liveAccountRefreshTimer = null;
let liveAccountRefreshInProgress = false;
let liveAccountInitialized = false;

const ACCOUNT_REQUEST_TIMEOUT_MS = 15_000;
const LIVE_ACCOUNT_REQUEST_TIMEOUT_MS = 8_000;
const LIVE_ACCOUNT_REFRESH_MS = 2_000;

const DEFAULT_STARTING_BALANCE = 100_000;

const moneyFormatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
});

const sharesFormatter = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 4,
});

const ALLOCATION_COLORS = [
    "#22c55e",
    "#3b82f6",
    "#a855f7",
    "#f59e0b",
    "#ef4444",
    "#06b6d4",
    "#ec4899",
    "#84cc16",
    "#64748b",
    "#f97316",
    "#14b8a6",
    "#8b5cf6",
];

async function loadAccount() {
    const positionsTable =
        document.getElementById("positions-table");

    const historyTable =
        document.getElementById("history-table");

    setTableMessage(
        positionsTable,
        7,
        "Loading positions…"
    );

    setTableMessage(
        historyTable,
        5,
        "Loading trade history…"
    );

    if (accountRequestController) {
        accountRequestController.abort();
    }

    accountRequestController =
        new AbortController();

    const timeoutId = window.setTimeout(
        () => accountRequestController?.abort(),
        ACCOUNT_REQUEST_TIMEOUT_MS
    );

    try {
        const account = await fetchJson(
            `${getApiUrl()}/account`,
            {
                signal:
                    accountRequestController.signal,
            }
        );

        const cash =
            toNumber(account.cash);

        const portfolioValue = toNumber(
            account.portfolio_value ??
            account.total_value ??
            account.equity ??
            cash
        );

        const startingBalance = toNumber(
            account.starting_cash ??
            account.starting_balance ??
            account.initial_balance ??
            DEFAULT_STARTING_BALANCE
        );

        const profitLoss = toNumber(
            account.profit_loss ??
            account.total_profit_loss ??
            portfolioValue - startingBalance
        );

        const profitLossPercent = toNumber(
            account.profit_loss_percent ??
            account.total_return_percent
        );

        const realizedProfitLoss = toNumber(
            account.realized_profit_loss
        );

        const unrealizedProfitLoss = toNumber(
            account.unrealized_profit_loss
        );

        const winRate = toNumber(
            account.win_rate
        );

        const closedTrades = Math.max(
            0,
            Math.trunc(
                toNumber(account.closed_trades)
            )
        );

        const cashPercent = toNumber(
            account.cash_percent
        );

        const investedPercent = toNumber(
            account.invested_percent
        );

        const highestPortfolioValue = toNumber(
            account.performance?.highest_value ??
            portfolioValue
        );

        const positions =
            normalizePositions(account.positions);

        const history = normalizeHistory(
            account.history ??
            account.trades ??
            []
        );

        setText(
            "cash",
            formatMoney(cash)
        );

        setText(
            "portfolio-value",
            formatMoney(portfolioValue)
        );

        setText(
            "profit-loss",
            formatSignedMoney(profitLoss)
        );

        setText(
            "position-count",
            String(positions.length)
        );

        setText(
            "total-return-percent",
            formatSignedPercentage(
                profitLossPercent
            )
        );

        setText(
            "realized-profit-loss",
            formatSignedMoney(
                realizedProfitLoss
            )
        );

        setText(
            "unrealized-profit-loss",
            formatSignedMoney(
                unrealizedProfitLoss
            )
        );

        setText(
            "win-rate",
            `${winRate.toFixed(2)}%`
        );

        setText(
            "closed-trades",
            String(closedTrades)
        );

        setText(
            "cash-percent",
            `${cashPercent.toFixed(2)}%`
        );

        setText(
            "invested-percent",
            `${investedPercent.toFixed(2)}%`
        );

        setText(
            "highest-portfolio-value",
            formatMoney(
                highestPortfolioValue
            )
        );

        updateProfitLossColor(profitLoss);

        updateMetricColor(
            "total-return-percent",
            profitLossPercent
        );

        updateMetricColor(
            "realized-profit-loss",
            realizedProfitLoss
        );

        updateMetricColor(
            "unrealized-profit-loss",
            unrealizedProfitLoss
        );

        renderPositions(
            positionsTable,
            positions
        );

        renderAllocationChart(
            positions,
            cash
        );

        renderHistory(
            historyTable,
            history
        );
    } catch (error) {
        if (error?.name === "AbortError") {
            console.warn(
                "Account request was cancelled or timed out."
            );
        } else {
            console.error(
                "Account error:",
                error
            );
        }

        setTableMessage(
            positionsTable,
            7,
            "Could not load account information."
        );

        setTableMessage(
            historyTable,
            5,
            "Could not load trade history."
        );

        showAllocationError();
    } finally {
        window.clearTimeout(timeoutId);
        accountRequestController = null;
    }
}

async function refreshLiveAccountMetrics() {
    if (
        document.hidden ||
        liveAccountRefreshInProgress
    ) {
        return;
    }

    liveAccountRefreshInProgress = true;

    liveAccountRequestController?.abort();

    liveAccountRequestController =
        new AbortController();

    const timeoutId = window.setTimeout(
        () =>
            liveAccountRequestController?.abort(),
        LIVE_ACCOUNT_REQUEST_TIMEOUT_MS
    );

    try {
        const account = await fetchJson(
            `${getApiUrl()}/account/live`,
            {
                signal:
                    liveAccountRequestController.signal,
                cache: "no-store",
            }
        );

        if (
            !account ||
            typeof account !== "object" ||
            account.error
        ) {
            throw new Error(
                account?.error ??
                "Live account snapshot was unavailable."
            );
        }

        const cash =
            toNumber(account.cash);

        const portfolioValue =
            toNumber(
                account.portfolio_value ??
                account.total_value ??
                account.equity ??
                cash
            );

        const startingBalance =
            toNumber(
                account.starting_cash ??
                account.starting_balance ??
                DEFAULT_STARTING_BALANCE
            );

        const profitLoss =
            toNumber(
                account.profit_loss ??
                account.total_profit_loss ??
                portfolioValue -
                    startingBalance
            );

        const profitLossPercent =
            toNumber(
                account.profit_loss_percent ??
                account.total_return_percent
            );

        const unrealizedProfitLoss =
            toNumber(
                account.unrealized_profit_loss
            );

        const cashPercent =
            toNumber(account.cash_percent);

        const investedPercent =
            toNumber(account.invested_percent);

        const positions =
            normalizePositions(
                account.positions
            );

        setText(
            "cash",
            formatMoney(cash)
        );

        setText(
            "portfolio-value",
            formatMoney(portfolioValue)
        );

        setText(
            "profit-loss",
            formatSignedMoney(profitLoss)
        );

        setText(
            "position-count",
            String(
                account.position_count ??
                account.open_positions ??
                positions.length
            )
        );

        setText(
            "total-return-percent",
            formatSignedPercentage(
                profitLossPercent
            )
        );

        setText(
            "unrealized-profit-loss",
            formatSignedMoney(
                unrealizedProfitLoss
            )
        );

        setText(
            "cash-percent",
            `${cashPercent.toFixed(2)}%`
        );

        setText(
            "invested-percent",
            `${investedPercent.toFixed(2)}%`
        );

        updateProfitLossColor(
            profitLoss
        );

        updateMetricColor(
            "total-return-percent",
            profitLossPercent
        );

        updateMetricColor(
            "unrealized-profit-loss",
            unrealizedProfitLoss
        );

        renderPositions(
            document.getElementById(
                "positions-table"
            ),
            positions
        );

        renderAllocationChart(
            positions,
            cash
        );

    } catch (error) {
        if (error?.name !== "AbortError") {
            console.warn(
                "Live account refresh failed:",
                error
            );
        }
    } finally {
        window.clearTimeout(timeoutId);

        liveAccountRequestController = null;
        liveAccountRefreshInProgress = false;
    }
}


function startLiveAccountRefresh() {
    if (liveAccountInitialized) {
        return;
    }

    liveAccountInitialized = true;

    refreshLiveAccountMetrics();

    liveAccountRefreshTimer =
        window.setInterval(
            refreshLiveAccountMetrics,
            LIVE_ACCOUNT_REFRESH_MS
        );

    document.addEventListener(
        "visibilitychange",
        () => {
            if (!document.hidden) {
                refreshLiveAccountMetrics();
            }
        }
    );

    window.addEventListener(
        "beforeunload",
        () => {
            if (liveAccountRefreshTimer) {
                window.clearInterval(
                    liveAccountRefreshTimer
                );
            }

            liveAccountRequestController?.abort();
        },
        {
            once: true,
        }
    );
}

async function fetchJson(
    url,
    options = {}
) {
    const response = await fetch(
        url,
        {
            headers: {
                Accept: "application/json",
                ...(options.headers || {}),
            },
            ...options,
        }
    );

    let payload = null;

    try {
        payload = await response.json();
    } catch {
        // Non-JSON error responses are handled below.
    }

    if (!response.ok) {
        const message =
            payload?.detail ??
            payload?.error ??
            `Request failed with status ${response.status}`;

        throw new Error(String(message));
    }

    if (
        !payload ||
        typeof payload !== "object"
    ) {
        throw new Error(
            "The server returned an invalid response."
        );
    }

    if (payload.error) {
        throw new Error(
            String(payload.error)
        );
    }

    return payload;
}

function getApiUrl() {
    const apiUrl = String(
        window.API_URL ?? ""
    ).replace(/\/+$/, "");

    if (!apiUrl) {
        throw new Error(
            "API_URL is not configured."
        );
    }

    return apiUrl;
}
function normalizePositions(positions) {
    if (!positions) {
        return [];
    }

    const list = Array.isArray(positions)
        ? positions
        : Object.entries(positions)
            .map(([symbol, position]) => ({
                symbol,
                ...(
                    position &&
                    typeof position === "object"
                        ? position
                        : {}
                ),
            }));

    return list
        .filter(
            position =>
                position &&
                typeof position === "object"
        )
        .map(getPositionDetails)
        .filter(
            position =>
                position.symbol &&
                position.shares > 0
        );
}

function getPositionDetails(position) {
    const symbol = String(
        position?.symbol ?? ""
    )
        .trim()
        .toUpperCase();

    const shares = toNumber(
        position?.shares ??
        position?.quantity
    );

    const entryPrice = toNumber(
        position?.entry_price ??
        position?.average_price ??
        position?.avg_price ??
        position?.price
    );

    const currentPrice = toNumber(
        position?.current_price ??
        position?.market_price ??
        position?.last_price ??
        entryPrice
    );

    const positionValue = toNumber(
        position?.position_value ??
        position?.market_value ??
        position?.value ??
        shares * currentPrice
    );

    const costBasis = toNumber(
        position?.cost_basis ??
        shares * entryPrice
    );

    const unrealizedProfit = toNumber(
        position?.unrealized_profit ??
        positionValue - costBasis
    );

    const unrealizedProfitPercent = toNumber(
        position?.unrealized_profit_percent ??
        (
            costBasis > 0
                ? (
                    unrealizedProfit /
                    costBasis
                ) * 100
                : 0
        )
    );

    return {
        symbol,
        shares,
        entryPrice,
        currentPrice,
        positionValue,
        costBasis,
        unrealizedProfit,
        unrealizedProfitPercent,
    };
}

function renderPositions(
    table,
    positions
) {
    if (!table) {
        return;
    }

    if (positions.length === 0) {
        setTableMessage(
            table,
            7,
            "No positions yet"
        );

        return;
    }

    table.innerHTML = positions
        .map(position => {
            const {
                symbol,
                shares,
                entryPrice,
                currentPrice,
                positionValue,
                unrealizedProfit,
                unrealizedProfitPercent,
            } = position;

            const gainLoss =
                unrealizedProfit;

            const returnPercentage =
                unrealizedProfitPercent;

            const performanceClass =
                getPerformanceClass(
                    gainLoss
                );

            return `
                <tr>
                    <td>
                        <button
                            type="button"
                            class="symbol-link"
                            data-position-symbol="${escapeHtml(symbol)}"
                            aria-label="Select ${escapeHtml(symbol)}"
                        >
                            ${escapeHtml(symbol)}
                        </button>
                    </td>

                    <td>
                        ${formatShares(shares)}
                    </td>

                    <td>
                        ${formatMoney(entryPrice)}
                    </td>

                    <td>
                        ${formatMoney(currentPrice)}
                    </td>

                    <td>
                        ${formatMoney(positionValue)}
                    </td>

                    <td class="${performanceClass}">
                        ${formatSignedMoney(gainLoss)}
                    </td>

                    <td class="${performanceClass}">
                        ${formatSignedPercentage(
                            returnPercentage
                        )}
                    </td>
                </tr>
            `;
        })
        .join("");
}

function renderAllocationChart(
    positions,
    cash
) {
    const canvas =
        document.getElementById(
            "allocation-chart"
        );

    const emptyMessage =
        document.getElementById(
            "allocation-empty"
        );

    if (!canvas) {
        return;
    }

    destroyAllocationChart();

    if (
        typeof window.Chart ===
        "undefined"
    ) {
        console.error(
            "Chart.js is not loaded."
        );

        showChartMessage(
            canvas,
            emptyMessage,
            "Could not load the allocation chart."
        );

        return;
    }

    const allocations = positions
        .filter(
            position =>
                position.symbol &&
                position.positionValue > 0
        )
        .map(position => ({
            label: position.symbol,
            value: position.positionValue,
        }));

    const cashValue =
        toNumber(cash);

    if (cashValue > 0) {
        allocations.push({
            label: "Cash",
            value: cashValue,
        });
    }

    if (allocations.length === 0) {
        showChartMessage(
            canvas,
            emptyMessage,
            "Make a trade to view your portfolio allocation."
        );

        return;
    }

    canvas.style.display =
        "block";

    if (emptyMessage) {
        emptyMessage.style.display =
            "none";
    }

    const labels =
        allocations.map(
            item => item.label
        );

    const values =
        allocations.map(
            item => item.value
        );

    allocationChart =
        new window.Chart(
            canvas.getContext("2d"),
            {
                type: "doughnut",

                data: {
                    labels,

                    datasets: [
                        {
                            data: values,

                            backgroundColor:
                                labels.map(
                                    (
                                        _,
                                        index
                                    ) =>
                                        ALLOCATION_COLORS[
                                            index %
                                            ALLOCATION_COLORS.length
                                        ]
                                ),

                            borderColor:
                                "#111827",

                            borderWidth: 3,

                            hoverOffset: 8,
                        },
                    ],
                },

                options: {
                    responsive: true,

                    maintainAspectRatio:
                        false,

                    cutout: "65%",

                    animation: {
                        duration: 500,
                    },

                    plugins: {
                        legend: {
                            position:
                                "bottom",

                            labels: {
                                color:
                                    "#d1d5db",

                                padding: 16,

                                usePointStyle:
                                    true,
                            },
                        },

                        tooltip: {
                            callbacks: {
                                label(context) {
                                    const value =
                                        toNumber(
                                            context.raw
                                        );

                                    const total =
                                        context
                                            .dataset
                                            .data
                                            .reduce(
                                                (
                                                    sum,
                                                    item
                                                ) =>
                                                    sum +
                                                    toNumber(
                                                        item
                                                    ),
                                                0
                                            );

                                    const percentage =
                                        total > 0
                                            ? (
                                                (
                                                    value /
                                                    total
                                                ) *
                                                100
                                            ).toFixed(
                                                1
                                            )
                                            : "0.0";

                                    return (
                                        `${context.label}: ` +
                                        `${formatMoney(value)} ` +
                                        `(${percentage}%)`
                                    );
                                },
                            },
                        },
                    },
                },
            }
        );
}
function showAllocationError() {
    const canvas =
        document.getElementById(
            "allocation-chart"
        );

    const emptyMessage =
        document.getElementById(
            "allocation-empty"
        );

    destroyAllocationChart();

    showChartMessage(
        canvas,
        emptyMessage,
        "Could not load portfolio allocation."
    );
}

function showChartMessage(
    canvas,
    messageElement,
    message
) {
    if (canvas) {
        canvas.style.display =
            "none";
    }

    if (messageElement) {
        messageElement.style.display =
            "block";

        messageElement.textContent =
            message;
    }
}

function destroyAllocationChart() {
    if (allocationChart) {
        allocationChart.destroy();
        allocationChart = null;
    }
}

function normalizeHistory(history) {
    if (!history) {
        return [];
    }

    const list = Array.isArray(history)
        ? history
        : Object.values(history);

    return list
        .filter(
            trade =>
                trade &&
                typeof trade === "object"
        )
        .map((trade, index) => {
            const symbol = String(
                trade.symbol ??
                trade.ticker ??
                ""
            )
                .trim()
                .toUpperCase();

            const rawSide = String(
                trade.side ??
                trade.action ??
                trade.type ??
                ""
            )
                .trim()
                .toUpperCase();

            const side =
                rawSide === "BUY" ||
                rawSide === "BOUGHT"
                    ? "BUY"
                    : rawSide === "SELL" ||
                      rawSide === "SOLD"
                        ? "SELL"
                        : rawSide || "UNKNOWN";

            const shares = toNumber(
                trade.shares ??
                trade.quantity ??
                trade.qty
            );

            const price = toNumber(
                trade.price ??
                trade.execution_price ??
                trade.fill_price ??
                trade.entry_price
            );

            const total = toNumber(
                trade.total ??
                trade.total_value ??
                trade.notional ??
                trade.amount ??
                shares * price
            );

            const profitLossDollars =
                trade.profit_loss_dollars === null ||
                trade.profit_loss_dollars === undefined
                    ? null
                    : toNumber(
                        trade.profit_loss_dollars
                    );

            const profitLossPercent =
                trade.profit_loss_percent === null ||
                trade.profit_loss_percent === undefined
                    ? null
                    : toNumber(
                        trade.profit_loss_percent
                    );

            const timestamp =
                trade.timestamp ??
                trade.created_at ??
                trade.executed_at ??
                trade.date ??
                trade.time ??
                null;

            return {
                id:
                    trade.id ??
                    trade.trade_id ??
                    index,

                symbol,
                side,
                shares,
                price,
                total,
                profitLossDollars,
                profitLossPercent,
                timestamp,

                timestampValue:
                    getTimestampValue(timestamp),
            };
        })
        .filter(
            trade =>
                trade.symbol &&
                trade.side !== "UNKNOWN"
        )
        .sort(
            (first, second) =>
                second.timestampValue -
                first.timestampValue
        );
}

function renderHistory(
    table,
    history
) {
    if (!table) {
        return;
    }

    if (history.length === 0) {
        setTableMessage(
            table,
            5,
            "No trades yet"
        );

        return;
    }

    table.innerHTML = history
        .map(trade => {
            const sideClass =
                trade.side === "BUY"
                    ? "positive"
                    : trade.side === "SELL"
                        ? "negative"
                        : "";

            const profitLossClass =
                trade.profitLossDollars === null
                    ? ""
                    : trade.profitLossDollars > 0
                        ? "positive"
                        : trade.profitLossDollars < 0
                            ? "negative"
                            : "";

            const profitLossDisplay =
                trade.profitLossDollars === null
                    ? "—"
                    : `${trade.profitLossDollars >= 0 ? "+" : ""}${formatMoney(
                        trade.profitLossDollars
                    )} (${trade.profitLossPercent >= 0 ? "+" : ""}${trade.profitLossPercent.toFixed(
                        2
                    )}%)`;

            return `
                <tr>
                    <td>
                        ${formatDateTime(
                            trade.timestamp
                        )}
                    </td>

                    <td>
                        <button
                            type="button"
                            class="symbol-link"
                            data-history-symbol="${escapeHtml(
                                trade.symbol
                            )}"
                            aria-label="Select ${escapeHtml(
                                trade.symbol
                            )}"
                        >
                            ${escapeHtml(
                                trade.symbol
                            )}
                        </button>
                    </td>

                    <td class="${sideClass}">
                        ${escapeHtml(
                            trade.side
                        )}
                        ${formatShares(
                            trade.shares
                        )}
                        @
                        ${formatMoney(
                            trade.price
                        )}
                    </td>

                    <td>
                        ${formatMoney(
                            trade.total
                        )}
                    </td>

                    <td class="${profitLossClass}">
                        ${profitLossDisplay}
                    </td>
                </tr>
            `;
        })
        .join("");
}

function getTimestampValue(value) {
    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return 0;
    }

    if (
        typeof value === "number" &&
        Number.isFinite(value)
    ) {
        const milliseconds =
            value < 10_000_000_000
                ? value * 1000
                : value;

        return milliseconds;
    }

    const parsed =
        Date.parse(String(value));

    return Number.isFinite(parsed)
        ? parsed
        : 0;
}

function formatDateTime(value) {
    const timestamp =
        getTimestampValue(value);

    if (!timestamp) {
        return "—";
    }

    const date =
        new Date(timestamp);

    if (
        Number.isNaN(
            date.getTime()
        )
    ) {
        return "—";
    }

    return new Intl.DateTimeFormat(
        "en-US",
        {
            month: "short",
            day: "numeric",
            year: "numeric",
            hour: "numeric",
            minute: "2-digit",
        }
    ).format(date);
}

function toNumber(value) {
    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return 0;
    }

    if (typeof value === "number") {
        return Number.isFinite(value)
            ? value
            : 0;
    }

    const normalized = String(value)
        .replace(/[$,%\s]/g, "")
        .replace(/,/g, "");

    const number =
        Number(normalized);

    return Number.isFinite(number)
        ? number
        : 0;
}

function formatMoney(value) {
    return moneyFormatter.format(
        toNumber(value)
    );
}

function formatSignedMoney(value) {
    const number =
        toNumber(value);

    if (number > 0) {
        return `+${moneyFormatter.format(
            number
        )}`;
    }

    return moneyFormatter.format(number);
}

function formatShares(value) {
    return sharesFormatter.format(
        toNumber(value)
    );
}

function formatSignedPercentage(value) {
    const number =
        toNumber(value);

    const prefix =
        number > 0
            ? "+"
            : "";

    return `${prefix}${number.toFixed(2)}%`;
}

function getPerformanceClass(value) {
    const number =
        toNumber(value);

    if (number > 0) {
        return "positive";
    }

    if (number < 0) {
        return "negative";
    }

    return "neutral";
}
function updateMetricColor(
    elementId,
    value
) {
    const element =
        document.getElementById(
            elementId
        );

    if (!element) {
        return;
    }

    element.classList.remove(
        "positive",
        "negative",
        "neutral"
    );

    element.classList.add(
        getPerformanceClass(value)
    );
}

function updateProfitLossColor(value) {
    const element =
        document.getElementById(
            "profit-loss"
        );

    if (!element) {
        return;
    }

    element.classList.remove(
        "positive",
        "negative",
        "neutral"
    );

    element.classList.add(
        getPerformanceClass(value)
    );
}

function setText(
    elementId,
    value
) {
    const element =
        document.getElementById(
            elementId
        );

    if (element) {
        element.textContent =
            String(value);
    }
}

function setTableMessage(
    table,
    columnCount,
    message
) {
    if (!table) {
        return;
    }

    table.innerHTML = `
        <tr>
            <td
                colspan="${Math.max(
                    1,
                    toNumber(columnCount)
                )}"
                class="table-message"
            >
                ${escapeHtml(message)}
            </td>
        </tr>
    `;
}

function escapeHtml(value) {
    return String(
        value ?? ""
    )
        .replace(
            /&/g,
            "&amp;"
        )
        .replace(
            /</g,
            "&lt;"
        )
        .replace(
            />/g,
            "&gt;"
        )
        .replace(
            /"/g,
            "&quot;"
        )
        .replace(
            /'/g,
            "&#039;"
        );
}

function selectSymbol(symbol) {
    const normalizedSymbol =
        String(symbol ?? "")
            .trim()
            .toUpperCase();

    if (!normalizedSymbol) {
        return;
    }

    const symbolInput =
        document.getElementById(
            "symbol"
        ) ??
        document.getElementById(
            "trade-symbol"
        );

    if (symbolInput) {
        symbolInput.value =
            normalizedSymbol;

        symbolInput.dispatchEvent(
            new Event(
                "input",
                {
                    bubbles: true,
                }
            )
        );

        symbolInput.focus();
    }

    if (
        typeof window.loadStock ===
        "function"
    ) {
        window.loadStock(
            normalizedSymbol
        );
    }
}

function handleAccountTableClick(event) {
    const positionButton =
        event.target.closest(
            "[data-position-symbol]"
        );

    if (positionButton) {
        selectSymbol(
            positionButton.dataset
                .positionSymbol
        );

        return;
    }

    const historyButton =
        event.target.closest(
            "[data-history-symbol]"
        );

    if (historyButton) {
        selectSymbol(
            historyButton.dataset
                .historySymbol
        );
    }
}

document.addEventListener(
    "click",
    handleAccountTableClick
);

window.loadAccount =
    loadAccount;

if (document.readyState === "loading") {
    document.addEventListener(
        "DOMContentLoaded",
        startLiveAccountRefresh,
        {
            once: true,
        }
    );
} else {
    startLiveAccountRefresh();
}