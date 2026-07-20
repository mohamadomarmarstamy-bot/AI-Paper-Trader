let allocationChart = null;


async function loadAccount() {
    const positionsTable =
        document.getElementById("positions-table");

    const historyTable =
        document.getElementById("history-table");

    try {
        const response = await fetch(`${API_URL}/account`);

        if (!response.ok) {
            throw new Error(
                `Account request failed: ${response.status}`
            );
        }

        const account = await response.json();

        if (account.error) {
            throw new Error(account.error);
        }

        const cash = toNumber(account.cash);

        const portfolioValue = toNumber(
            account.portfolio_value ??
            account.total_value ??
            cash
        );

        const profitLoss = toNumber(
            account.profit_loss ??
            portfolioValue - 100000
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

        updateProfitLossColor(profitLoss);

        const positions = normalizePositions(
            account.positions
        );

        setText(
            "position-count",
            String(positions.length)
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
            account.history || []
        );

    } catch (error) {
        console.error("Account error:", error);

        if (positionsTable) {
            positionsTable.innerHTML = `
                <tr>
                    <td colspan="4">
                        Could not load account information.
                    </td>
                </tr>
            `;
        }

        if (historyTable) {
            historyTable.innerHTML = `
                <tr>
                    <td colspan="4">
                        Could not load trade history.
                    </td>
                </tr>
            `;
        }

        showAllocationError();
    }
}


function normalizePositions(positions) {
    if (!positions) {
        return [];
    }

    let normalizedPositions;

    if (Array.isArray(positions)) {
        normalizedPositions = positions;
    } else {
        normalizedPositions = Object.entries(
            positions
        ).map(([symbol, position]) => ({
            symbol,
            ...position
        }));
    }

    return normalizedPositions.filter(position => {
        const shares = toNumber(
            position.shares ??
            position.quantity
        );

        return shares > 0;
    });
}


function getPositionDetails(position) {
    const symbol = String(
        position.symbol || ""
    ).trim().toUpperCase();

    const shares = toNumber(
        position.shares ??
        position.quantity
    );

    const entryPrice = toNumber(
        position.entry_price ??
        position.average_price ??
        position.price
    );

    const currentPrice = toNumber(
        position.current_price ??
        position.market_price ??
        entryPrice
    );

    const positionValue = toNumber(
        position.position_value ??
        position.market_value ??
        position.value ??
        shares * currentPrice
    );

    return {
        symbol,
        shares,
        entryPrice,
        currentPrice,
        positionValue
    };
}


function renderPositions(table, positions) {
    if (!table) {
        return;
    }

    if (positions.length === 0) {
        table.innerHTML = `
            <tr>
                <td colspan="7">
                    No positions yet
                </td>
            </tr>
        `;

        return;
    }

    table.innerHTML = positions.map(position => {
        const {
            symbol,
            shares,
            entryPrice,
            currentPrice,
            positionValue
        } = getPositionDetails(position);

        const costBasis =
            shares * entryPrice;

        const gainLoss =
            positionValue - costBasis;

        const returnPercentage =
            costBasis > 0
                ? gainLoss / costBasis * 100
                : 0;

        let performanceClass = "";

        if (gainLoss > 0) {
            performanceClass = "positive";
        } else if (gainLoss < 0) {
            performanceClass = "negative";
        }

        return `
            <tr>
                <td>
                    <button
                        type="button"
                        class="symbol-link"
                        data-position-symbol="${escapeHtml(symbol)}"
                    >
                        ${escapeHtml(symbol)}
                    </button>
                </td>

                <td>${formatShares(shares)}</td>

                <td>${formatMoney(entryPrice)}</td>

                <td>${formatMoney(currentPrice)}</td>

                <td>${formatMoney(positionValue)}</td>

                <td class="${performanceClass}">
                    ${formatSignedMoney(gainLoss)}
                </td>

                <td class="${performanceClass}">
                    ${formatSignedPercentage(returnPercentage)}
                </td>
            </tr>
        `;
    }).join("");
}


function renderAllocationChart(positions, cash) {
    const canvas =
        document.getElementById("allocation-chart");

    const emptyMessage =
        document.getElementById("allocation-empty");

    if (!canvas) {
        return;
    }

    if (typeof Chart === "undefined") {
        console.error(
            "Chart.js is not loaded. Check the Chart.js script tag."
        );

        canvas.style.display = "none";

        if (emptyMessage) {
            emptyMessage.style.display = "block";
            emptyMessage.textContent =
                "Could not load the allocation chart.";
        }

        return;
    }

    const labels = [];
    const values = [];

    positions.forEach(position => {
        const {
            symbol,
            positionValue
        } = getPositionDetails(position);

        if (symbol && positionValue > 0) {
            labels.push(symbol);
            values.push(positionValue);
        }
    });

    const cashValue = toNumber(cash);

    if (cashValue > 0) {
        labels.push("Cash");
        values.push(cashValue);
    }

    const hasAllocationData =
        labels.length > 0 &&
        values.some(value => value > 0);

    if (!hasAllocationData) {
        canvas.style.display = "none";

        if (emptyMessage) {
            emptyMessage.style.display = "block";
            emptyMessage.textContent =
                "Make a trade to view your portfolio allocation.";
        }

        if (allocationChart) {
            allocationChart.destroy();
            allocationChart = null;
        }

        return;
    }

    canvas.style.display = "block";

    if (emptyMessage) {
        emptyMessage.style.display = "none";
    }

    if (allocationChart) {
        allocationChart.destroy();
        allocationChart = null;
    }

    allocationChart = new Chart(
        canvas.getContext("2d"),
        {
            type: "doughnut",

            data: {
                labels,

                datasets: [
                    {
                        data: values,

                        backgroundColor: [
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
                            "#8b5cf6"
                        ],

                        borderColor: "#111827",
                        borderWidth: 3,
                        hoverOffset: 8
                    }
                ]
            },

            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "65%",

                animation: {
                    duration: 500
                },

                plugins: {
                    legend: {
                        position: "bottom",

                        labels: {
                            color: "#d1d5db",
                            padding: 16,
                            usePointStyle: true
                        }
                    },

                    tooltip: {
                        callbacks: {
                            label(context) {
                                const value = toNumber(
                                    context.raw
                                );

                                const total =
                                    context.dataset.data.reduce(
                                        (sum, item) =>
                                            sum + toNumber(item),
                                        0
                                    );

                                const percentage =
                                    total > 0
                                        ? (
                                            value /
                                            total *
                                            100
                                        ).toFixed(1)
                                        : "0.0";

                                return (
                                    `${context.label}: ` +
                                    `${formatMoney(value)} ` +
                                    `(${percentage}%)`
                                );
                            }
                        }
                    }
                }
            }
        }
    );
}


function showAllocationError() {
    const canvas =
        document.getElementById("allocation-chart");

    const emptyMessage =
        document.getElementById("allocation-empty");

    if (allocationChart) {
        allocationChart.destroy();
        allocationChart = null;
    }

    if (canvas) {
        canvas.style.display = "none";
    }

    if (emptyMessage) {
        emptyMessage.style.display = "block";
        emptyMessage.textContent =
            "Could not load portfolio allocation.";
    }
}


function renderHistory(table, history) {
    if (!table) {
        return;
    }

    if (!Array.isArray(history) || history.length === 0) {
        table.innerHTML = `
            <tr>
                <td colspan="4">
                    No trades yet
                </td>
            </tr>
        `;

        return;
    }

    table.innerHTML = [...history]
        .reverse()
        .map(trade => {
            const action = String(
                trade.action ??
                trade.type ??
                ""
            ).toUpperCase();

            const symbol = String(
                trade.symbol || ""
            ).toUpperCase();

            const shares = toNumber(
                trade.shares ??
                trade.quantity
            );

            const hasProfit =
                trade.profit !== undefined &&
                trade.profit !== null;

            const displayedValue = toNumber(
                hasProfit
                    ? trade.profit
                    : trade.price
            );

            let valueClass = "";

            if (hasProfit && displayedValue > 0) {
                valueClass = "positive";
            } else if (
                hasProfit &&
                displayedValue < 0
            ) {
                valueClass = "negative";
            }

            return `
                <tr>
                    <td>${escapeHtml(action)}</td>

                    <td>${escapeHtml(symbol)}</td>

                    <td>${formatShares(shares)}</td>

                    <td class="${valueClass}">
                        ${
                            hasProfit
                                ? formatSignedMoney(displayedValue)
                                : formatMoney(displayedValue)
                        }
                    </td>
                </tr>
            `;
        })
        .join("");
}


async function selectPositionStock(symbol) {
    const symbolInput =
        document.getElementById("symbol");

    const priceInput =
        document.getElementById("price");

    if (symbolInput) {
        symbolInput.value = symbol;
    }

    if (typeof window.loadChart === "function") {
        window.loadChart(symbol);
    }

    try {
        const response = await fetch(
            `${API_URL}/quote/${encodeURIComponent(symbol)}`
        );

        if (!response.ok) {
            throw new Error(
                `Quote request failed: ${response.status}`
            );
        }

        const quote = await response.json();

        if (quote.error) {
            throw new Error(quote.error);
        }

        const price = toNumber(quote.price);

        if (priceInput && price > 0) {
            priceInput.value = price.toFixed(2);
        }

    } catch (error) {
        console.error(
            `Could not select ${symbol}:`,
            error
        );
    }
}


function updateProfitLossColor(value) {
    const element =
        document.getElementById("profit-loss");

    if (!element) {
        return;
    }

    element.classList.remove(
        "positive",
        "negative"
    );

    if (value > 0) {
        element.classList.add("positive");
    } else if (value < 0) {
        element.classList.add("negative");
    }
}


function setText(elementId, value) {
    const element =
        document.getElementById(elementId);

    if (element) {
        element.textContent = value;
    }
}


function toNumber(value) {
    const number = Number(value);

    return Number.isFinite(number)
        ? number
        : 0;
}


function formatMoney(value) {
    return toNumber(value).toLocaleString(
        "en-US",
        {
            style: "currency",
            currency: "USD"
        }
    );
}


function formatSignedMoney(value) {
    const amount = toNumber(value);

    const prefix =
        amount > 0
            ? "+"
            : "";

    return `${prefix}${formatMoney(amount)}`;
}

function formatSignedPercentage(value) {
    const percentage = toNumber(value);
    const prefix = percentage > 0 ? "+" : "";

    return `${prefix}${percentage.toFixed(2)}%`;
}

function formatShares(value) {
    return toNumber(value).toLocaleString(
        "en-US",
        {
            maximumFractionDigits: 4
        }
    );
}


function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


window.addEventListener(
    "DOMContentLoaded",
    () => {
        const positionsTable =
            document.getElementById("positions-table");

        if (positionsTable) {
            positionsTable.addEventListener(
                "click",
                event => {
                    const button =
                        event.target.closest(
                            "[data-position-symbol]"
                        );

                    if (!button) {
                        return;
                    }

                    selectPositionStock(
                        button.dataset.positionSymbol
                    );
                }
            );
        }

        loadAccount();
    }
);