"use strict";

(() => {
// ==========================================
// Trade Center
// ==========================================

let tradeInProgress = false;

let quoteRefreshTimer = null;
let riskRefreshTimer = null;

let activeTradeQuoteRequest = null;
let activeRiskRequest = null;
let activeTradeRequest = null;

let latestTradeQuoteRequestNumber = 0;
let latestRiskRequestNumber = 0;

let currentTradeQuote = null;
let currentRiskPlan = null;

const TRADE_QUOTE_DEBOUNCE_MS = 500;
const TRADE_RISK_DEBOUNCE_MS = 650;

const QUOTE_REQUEST_TIMEOUT_MS = 12_000;
const RISK_REQUEST_TIMEOUT_MS = 15_000;
const TRADE_REQUEST_TIMEOUT_MS = 30_000;

const tradeCurrencyFormatter = new Intl.NumberFormat(
    "en-US",
    {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }
);

const tradeNumberFormatter = new Intl.NumberFormat(
    "en-US",
    {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2
    }
);


// ==========================================
// Startup
// ==========================================

document.addEventListener(
    "DOMContentLoaded",
    initializeTradeCenter
);


function initializeTradeCenter() {
    const symbolInput =
        document.getElementById("symbol");

    const sharesInput =
        document.getElementById("shares");

    const priceInput =
        document.getElementById("price");

    if (!symbolInput) {
        return;
    }

    createTradeCenterPanels();

    symbolInput.addEventListener(
        "input",
        handleTradeSymbolInput
    );

    symbolInput.addEventListener(
        "change",
        handleTradeSymbolChange
    );

    symbolInput.addEventListener(
        "blur",
        normalizeTradeSymbolInput
    );

    sharesInput?.addEventListener(
        "input",
        updateTradePreview
    );

    priceInput?.addEventListener(
        "input",
        handleManualPriceInput
    );

    priceInput?.addEventListener(
        "change",
        handleManualPriceInput
    );

    document.addEventListener(
        "search:stock-selected",
        handleExternalStockSelection
    );

    document.addEventListener(
        "scanner:stock-selected",
        handleExternalStockSelection
    );

    bindTradeButtons();

    const initialSymbol =
        normalizeTradeSymbol(
            symbolInput.value
        );

    if (initialSymbol) {
        scheduleTradeDataRefresh(
            initialSymbol,
            0
        );
    } else {
        updateTradePreview();
        renderRiskPlan(null);
    }
}


// ==========================================
// Input handling
// ==========================================

function handleTradeSymbolInput() {
    clearTimeout(quoteRefreshTimer);
    clearTimeout(riskRefreshTimer);

    const symbol =
        getTradeSymbol();

    currentTradeQuote = null;
    currentRiskPlan = null;

    if (!symbol) {
        cancelactiveTradeQuoteRequest();
        cancelActiveRiskRequest();

        clearTradePrice();
        renderRiskPlan(null);
        updateTradePreview();

        return;
    }

    scheduleTradeDataRefresh(
        symbol,
        TRADE_QUOTE_DEBOUNCE_MS
    );
}


function handleTradeSymbolChange() {
    clearTimeout(quoteRefreshTimer);
    clearTimeout(riskRefreshTimer);

    normalizeTradeSymbolInput();

    const symbol =
        getTradeSymbol();

    if (!symbol) {
        clearTradePrice();
        renderRiskPlan(null);
        updateTradePreview();

        return;
    }

    scheduleTradeDataRefresh(
        symbol,
        0
    );
}


function normalizeTradeSymbolInput() {
    const symbolInput =
        document.getElementById("symbol");

    if (!symbolInput) {
        return;
    }

    symbolInput.value =
        normalizeTradeSymbol(
            symbolInput.value
        );
}


function handleManualPriceInput() {
    const price =
        getTradePrice();

    currentTradeQuote =
        price !== null
            ? {
                symbol: getTradeSymbol(),
                price
            }
            : null;

    updateTradePreview();
}


function handleExternalStockSelection(event) {
    const detail =
        event?.detail ?? {};

    const symbol =
        normalizeTradeSymbol(
            detail.symbol
        );

    const price =
        toPositiveTradeNumber(
            detail.price
        );

    if (!symbol) {
        return;
    }

    const symbolInput =
        document.getElementById("symbol");

    const priceInput =
        document.getElementById("price");

    if (symbolInput) {
        symbolInput.value =
            symbol;
    }

    if (
        priceInput &&
        price !== null
    ) {
        priceInput.value =
            price.toFixed(2);

        priceInput.placeholder =
            "0.00";

        priceInput.classList.remove(
            "error"
        );

        currentTradeQuote = {
            symbol,
            price
        };
    }

    clearTimeout(quoteRefreshTimer);
    clearTimeout(riskRefreshTimer);

    riskRefreshTimer =
        window.setTimeout(
            () => {
                loadRiskPlan(symbol);
            },
            0
        );

    updateTradePreview();
}


function scheduleTradeDataRefresh(
    symbol,
    delay
) {
    clearTimeout(quoteRefreshTimer);
    clearTimeout(riskRefreshTimer);

    quoteRefreshTimer =
        window.setTimeout(
            () => {
                loadLivePrice(symbol);
            },
            delay
        );

    riskRefreshTimer =
        window.setTimeout(
            () => {
                loadRiskPlan(symbol);
            },
            delay + TRADE_RISK_DEBOUNCE_MS
        );
}


// ==========================================
// Quote loading
// ==========================================

async function loadLivePrice(
    requestedSymbol = null
) {
    const priceInput =
        document.getElementById("price");

    if (!priceInput) {
        return null;
    }

    const symbol =
        normalizeTradeSymbol(
            requestedSymbol ??
            getTradeSymbol()
        );

    if (!symbol) {
        clearTradePrice();

        return null;
    }

    cancelactiveTradeQuoteRequest();

    const controller =
        new AbortController();

    activeTradeQuoteRequest =
        controller;

    const requestNumber =
        ++latestTradeQuoteRequestNumber;

    let requestTimedOut = false;

    const timeoutId =
        window.setTimeout(
            () => {
                requestTimedOut = true;
                controller.abort();
            },
            QUOTE_REQUEST_TIMEOUT_MS
        );

    setTradePriceLoading();

    try {
        const response = await fetch(
            `${getTradeApiUrl()}/quote/${encodeURIComponent(
                symbol
            )}`,
            {
                method: "GET",

                headers: {
                    Accept:
                        "application/json"
                },

                signal:
                    controller.signal
            }
        );

        const result =
            await readTradeJson(
                response
            );

        if (!response.ok) {
            throw new Error(
                getTradeResponseMessage(
                    result,
                    `Unable to fetch the live price. HTTP ${response.status}.`
                )
            );
        }

        if (
            requestNumber !==
            latestTradeQuoteRequestNumber
        ) {
            return null;
        }

        const price =
            normalizeQuotePrice(
                result
            );

        if (price === null) {
            throw new Error(
                "No valid live price was returned for this symbol."
            );
        }

        if (
            symbol !==
            getTradeSymbol()
        ) {
            return null;
        }

        currentTradeQuote = {
            symbol,
            price
        };

        priceInput.value =
            price.toFixed(2);

        priceInput.placeholder =
            "0.00";

        priceInput.classList.remove(
            "error"
        );

        priceInput.removeAttribute(
            "aria-busy"
        );

        clearTradeMessageIfError();
        updateTradePreview();

        return price;
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            if (
                requestNumber !==
                latestTradeQuoteRequestNumber
            ) {
                return null;
            }

            if (!requestTimedOut) {
                return null;
            }

            setTradePriceError(
                "Price request timed out."
            );

            return null;
        }

        console.error(
            "Quote error:",
            error
        );

        if (
            requestNumber ===
            latestTradeQuoteRequestNumber
        ) {
            setTradePriceError(
                error?.message ||
                "Price unavailable."
            );
        }

        return null;
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeTradeQuoteRequest ===
            controller
        ) {
            activeTradeQuoteRequest =
                null;
        }
    }
}


// ==========================================
// Risk plan
// ==========================================

async function loadRiskPlan(
    requestedSymbol = null
) {
    const symbol =
        normalizeTradeSymbol(
            requestedSymbol ??
            getTradeSymbol()
        );

    if (!symbol) {
        currentRiskPlan = null;
        renderRiskPlan(null);

        return null;
    }

    cancelActiveRiskRequest();

    const controller =
        new AbortController();

    activeRiskRequest =
        controller;

    const requestNumber =
        ++latestRiskRequestNumber;

    let requestTimedOut = false;

    const timeoutId =
        window.setTimeout(
            () => {
                requestTimedOut = true;
                controller.abort();
            },
            RISK_REQUEST_TIMEOUT_MS
        );

    renderRiskPlanLoading(symbol);

    try {
        const response = await fetch(
            `${getTradeApiUrl()}/risk-plan/${encodeURIComponent(
                symbol
            )}`,
            {
                method: "GET",

                headers: {
                    Accept:
                        "application/json"
                },

                signal:
                    controller.signal
            }
        );

        const result =
            await readTradeJson(
                response
            );

        if (!response.ok) {
            throw new Error(
                getTradeResponseMessage(
                    result,
                    `Risk plan request failed. HTTP ${response.status}.`
                )
            );
        }

        if (
            requestNumber !==
            latestRiskRequestNumber
        ) {
            return null;
        }

        if (
            symbol !==
            getTradeSymbol()
        ) {
            return null;
        }

        currentRiskPlan =
            normalizeRiskPlan(
                result,
                symbol
            );

        renderRiskPlan(
            currentRiskPlan
        );

        applySuggestedShares(
            currentRiskPlan
        );

        updateTradePreview();

        return currentRiskPlan;
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            if (
                requestNumber !==
                latestRiskRequestNumber
            ) {
                return null;
            }

            if (!requestTimedOut) {
                return null;
            }

            renderRiskPlanError(
                "Risk analysis timed out."
            );

            return null;
        }

        console.error(
            "Risk plan error:",
            error
        );

        if (
            requestNumber ===
            latestRiskRequestNumber
        ) {
            currentRiskPlan = null;

            renderRiskPlanError(
                error?.message ||
                "Risk analysis is currently unavailable."
            );
        }

        return null;
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeRiskRequest ===
            controller
        ) {
            activeRiskRequest =
                null;
        }
    }
}


function normalizeRiskPlan(
    payload,
    fallbackSymbol
) {
    const source =
        payload?.plan ??
        payload?.risk_plan ??
        payload?.data ??
        payload ??
        {};

    const entryPrice =
        firstPositiveTradeNumber(
            source.entry_price,
            source.entry,
            source.price,
            currentTradeQuote?.price,
            getTradePrice()
        );

    const stopLoss =
        firstPositiveTradeNumber(
            source.stop_loss,
            source.stop,
            source.suggested_stop
        );

    const takeProfit =
        firstPositiveTradeNumber(
            source.take_profit,
            source.target,
            source.price_target,
            source.suggested_target
        );

    const suggestedShares =
        firstPositiveInteger(
            source.suggested_shares,
            source.position_size,
            source.shares,
            source.quantity
        );

    const riskReward =
        firstPositiveTradeNumber(
            source.risk_reward,
            source.risk_reward_ratio,
            source.rr_ratio
        ) ??
        calculateRiskReward(
            entryPrice,
            stopLoss,
            takeProfit
        );

    return {
        symbol:
            normalizeTradeSymbol(
                source.symbol ??
                fallbackSymbol
            ),

        entryPrice,
        stopLoss,
        takeProfit,
        suggestedShares,
        riskReward,

        riskPercent:
            firstFiniteTradeNumber(
                source.risk_percent,
                source.risk_percentage,
                source.account_risk_percent
            ),

        riskAmount:
            firstPositiveTradeNumber(
                source.risk_amount,
                source.maximum_loss,
                source.max_loss
            ),

        potentialProfit:
            firstPositiveTradeNumber(
                source.potential_profit,
                source.expected_profit
            ),

        riskLevel:
            normalizeRiskLevel(
                source.risk_level ??
                source.risk
            ),

        recommendation:
            normalizeTradeText(
                source.recommendation ??
                source.rating ??
                source.signal
            ),

        confidence:
            clampTradeNumber(
                source.confidence ??
                source.ai_confidence,
                0,
                100,
                null
            ),

        summary:
            normalizeTradeText(
                source.ai_summary ??
                source.summary ??
                source.analysis ??
                source.explanation
            )
    };
}


// ==========================================
// Trade execution
// ==========================================

async function buyStock() {
    return submitTrade("buy");
}


async function sellStock() {
    return submitTrade("sell");
}


async function submitTrade(action) {
    if (tradeInProgress) {
        return;
    }

    const cleanAction =
        String(action)
            .trim()
            .toLowerCase();

    if (
        cleanAction !== "buy" &&
        cleanAction !== "sell"
    ) {
        showTradeMessage(
            "The requested trade action is invalid.",
            "error"
        );

        return;
    }

    const symbol =
        getTradeSymbol();

    const shares =
        getTradeShares();

    const validationError =
        validateTradeInput(
            symbol,
            shares
        );

    if (validationError) {
        showTradeMessage(
            validationError,
            "error"
        );

        focusInvalidTradeField(
            symbol,
            shares
        );

        return;
    }

    cancelActiveTradeRequest();

    const controller =
        new AbortController();

    activeTradeRequest =
        controller;

    let requestTimedOut = false;

    const timeoutId =
        window.setTimeout(
            () => {
                requestTimedOut = true;
                controller.abort();
            },
            TRADE_REQUEST_TIMEOUT_MS
        );

    tradeInProgress = true;

    setTradeButtonsDisabled(
        true,
        cleanAction
    );

    const actionLabel =
        cleanAction === "buy"
            ? "Buying"
            : "Selling";

    showTradeMessage(
        `${actionLabel} ${shares} share${shares === 1 ? "" : "s"} of ${symbol} at the current market price…`,
        ""
    );

    try {
        const response = await fetch(
            `${getTradeApiUrl()}/${cleanAction}`,
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json",

                    Accept:
                        "application/json"
                },

                body: JSON.stringify({
                    symbol,
                    shares
                }),

                signal:
                    controller.signal
            }
        );

        const result =
            await readTradeJson(
                response
            );

        if (!response.ok) {
            throw new Error(
                getTradeResponseMessage(
                    result,
                    `Trade request failed. HTTP ${response.status}.`
                )
            );
        }

        if (
            result?.success === false ||
            result?.error
        ) {
            throw new Error(
                getTradeResponseMessage(
                    result,
                    "The trade could not be completed."
                )
            );
        }

        const execution =
            normalizeTradeExecution(
                result,
                symbol,
                shares,
                cleanAction
            );

        showTradeSuccess(
            execution
        );

        clearSharesInput();

        await refreshAfterTrade(
            symbol
        );
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            showTradeMessage(
                requestTimedOut
                    ? "The trade request timed out. Check your account before submitting it again."
                    : "The trade request was cancelled.",
                "error"
            );

            return;
        }

        console.error(
            "Trade error:",
            error
        );

        showTradeMessage(
            error?.message ||
            "Unable to complete the trade.",
            "error"
        );
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeTradeRequest ===
            controller
        ) {
            activeTradeRequest =
                null;
        }

        tradeInProgress = false;

        setTradeButtonsDisabled(
            false
        );
    }
}


function validateTradeInput(
    symbol,
    shares
) {
    if (!symbol) {
        return "Please enter a valid stock symbol.";
    }

    if (
        shares === null ||
        !Number.isInteger(shares) ||
        shares <= 0
    ) {
        return "Please enter a whole number of shares greater than zero.";
    }

    if (shares > 1_000_000) {
        return "The requested share quantity is too large.";
    }

    return "";
}


function normalizeTradeExecution(
    payload,
    symbol,
    shares,
    action
) {
    const source =
        payload?.trade ??
        payload?.order ??
        payload ??
        {};

    return {
        symbol:
            normalizeTradeSymbol(
                source.symbol ??
                symbol
            ),

        shares:
            firstPositiveInteger(
                source.shares,
                source.quantity,
                shares
            ) ?? shares,

        action:
            normalizeTradeText(
                source.action ??
                action
            ).toUpperCase(),

        price:
            firstPositiveTradeNumber(
                source.price,
                source.execution_price,
                source.average_price,
                source.avg_price,
                currentTradeQuote?.price,
                getTradePrice()
            ),

        total:
            firstPositiveTradeNumber(
                source.total,
                source.total_value,
                source.cost,
                source.proceeds
            ),

        message:
            normalizeTradeText(
                payload?.message ??
                source.message
            )
    };
}


// ==========================================
// Refresh application after trade
// ==========================================

async function refreshAfterTrade(
    symbol
) {
    const refreshTasks = [];

    addTradeRefreshTask(
        refreshTasks,
        window.loadAccount
    );

    addTradeRefreshTask(
        refreshTasks,
        window.loadWatchlist
    );

    addTradeRefreshTask(
        refreshTasks,
        window.loadPortfolioChart
    );

    addTradeRefreshTask(
        refreshTasks,
        window.loadScanner
    );

    addTradeRefreshTask(
        refreshTasks,
        window.refreshDashboard
    );

    refreshTasks.push(
        loadLivePrice(symbol)
    );

    refreshTasks.push(
        loadRiskPlan(symbol)
    );

    const results =
        await Promise.allSettled(
            refreshTasks
        );

    results.forEach(
        result => {
            if (
                result.status ===
                "rejected"
            ) {
                console.warn(
                    "A post-trade refresh failed:",
                    result.reason
                );
            }
        }
    );

    if (
        typeof window.loadChart ===
        "function"
    ) {
        try {
            window.loadChart(symbol);
        } catch (error) {
            console.warn(
                "Chart refresh failed:",
                error
            );
        }
    }

    document.dispatchEvent(
        new CustomEvent(
            "trade:completed",
            {
                detail: {
                    symbol
                }
            }
        )
    );
}


function addTradeRefreshTask(
    taskList,
    callback
) {
    if (
        typeof callback !==
        "function"
    ) {
        return;
    }

    taskList.push(
        Promise.resolve()
            .then(() => callback())
    );
}


// ==========================================
// Trade preview
// ==========================================

function updateTradePreview() {
    const preview =
        document.getElementById(
            "trade-preview"
        );

    if (!preview) {
        return;
    }

    const symbol =
        getTradeSymbol();

    const shares =
        getTradeShares();

    const price =
        currentTradeQuote?.symbol ===
        symbol
            ? currentTradeQuote.price
            : getTradePrice();

    preview.replaceChildren();

    const title =
        document.createElement("h3");

    title.className =
        "trade-panel-title";

    title.textContent =
        "Order Preview";

    preview.appendChild(title);

    if (
        !symbol ||
        shares === null ||
        shares <= 0 ||
        price === null
    ) {
        const empty =
            document.createElement("p");

        empty.className =
            "trade-preview-empty";

        empty.textContent =
            "Enter a symbol and number of shares to preview the order.";

        preview.appendChild(empty);

        return;
    }

    const estimatedValue =
        price * shares;

    const stopLoss =
        currentRiskPlan?.stopLoss ??
        null;

    const takeProfit =
        currentRiskPlan?.takeProfit ??
        null;

    const potentialLoss =
        stopLoss !== null &&
        stopLoss < price
            ? (
                price -
                stopLoss
            ) * shares
            : null;

    const potentialProfit =
        takeProfit !== null &&
        takeProfit > price
            ? (
                takeProfit -
                price
            ) * shares
            : null;

    const riskReward =
        potentialLoss !== null &&
        potentialLoss > 0 &&
        potentialProfit !== null
            ? potentialProfit /
              potentialLoss
            : currentRiskPlan
                ?.riskReward ??
              null;

    const grid =
        document.createElement("div");

    grid.className =
        "trade-preview-grid";

    grid.append(
        createTradeMetric(
            "Symbol",
            symbol
        ),

        createTradeMetric(
            "Shares",
            tradeNumberFormatter.format(
                shares
            )
        ),

        createTradeMetric(
            "Market Price",
            formatTradeCurrency(
                price
            )
        ),

        createTradeMetric(
            "Estimated Value",
            formatTradeCurrency(
                estimatedValue
            )
        ),

        createTradeMetric(
            "Potential Loss",
            potentialLoss !== null
                ? formatTradeCurrency(
                    potentialLoss
                )
                : "—"
        ),

        createTradeMetric(
            "Potential Profit",
            potentialProfit !== null
                ? formatTradeCurrency(
                    potentialProfit
                )
                : "—"
        ),

        createTradeMetric(
            "Risk / Reward",
            riskReward !== null
                ? `${formatTradeNumber(
                    riskReward,
                    2
                )}:1`
                : "—"
        )
    );

    preview.appendChild(grid);
}


// ==========================================
// Risk-plan rendering
// ==========================================

function renderRiskPlan(plan) {
    const panel =
        document.getElementById(
            "trade-risk-plan"
        );

    if (!panel) {
        return;
    }

    panel.replaceChildren();

    const title =
        document.createElement("h3");

    title.className =
        "trade-panel-title";

    title.textContent =
        "Risk Plan";

    panel.appendChild(title);

    if (!plan) {
        const empty =
            document.createElement("p");

        empty.className =
            "trade-risk-empty";

        empty.textContent =
            "Select a stock to load its suggested risk plan.";

        panel.appendChild(empty);

        return;
    }

    const grid =
        document.createElement("div");

    grid.className =
        "trade-risk-grid";

    grid.append(
        createTradeMetric(
            "Entry",
            formatTradeCurrency(
                plan.entryPrice
            )
        ),

        createTradeMetric(
            "Stop Loss",
            formatTradeCurrency(
                plan.stopLoss
            )
        ),

        createTradeMetric(
            "Target",
            formatTradeCurrency(
                plan.takeProfit
            )
        ),

        createTradeMetric(
            "Risk / Reward",
            plan.riskReward !== null
                ? `${formatTradeNumber(
                    plan.riskReward,
                    2
                )}:1`
                : "—"
        ),

        createTradeMetric(
            "Suggested Shares",
            plan.suggestedShares !== null
                ? tradeNumberFormatter.format(
                    plan.suggestedShares
                )
                : "—"
        ),

        createTradeMetric(
            "Risk Level",
            plan.riskLevel
        )
    );

    panel.appendChild(grid);

    if (
        plan.recommendation ||
        plan.confidence !== null
    ) {
        const recommendation =
            document.createElement("div");

        recommendation.className =
            "trade-recommendation";

        const recommendationText = [];

        if (plan.recommendation) {
            recommendationText.push(
                plan.recommendation
            );
        }

        if (plan.confidence !== null) {
            recommendationText.push(
                `${Math.round(
                    plan.confidence
                )}% confidence`
            );
        }

        recommendation.textContent =
            recommendationText.join(
                " · "
            );

        panel.appendChild(
            recommendation
        );
    }

    if (plan.summary) {
        const summary =
            document.createElement("p");

        summary.className =
            "trade-risk-summary";

        summary.textContent =
            plan.summary;

        panel.appendChild(summary);
    }
}


function renderRiskPlanLoading(
    symbol
) {
    const panel =
        document.getElementById(
            "trade-risk-plan"
        );

    if (!panel) {
        return;
    }

    panel.replaceChildren();

    const status =
        document.createElement("p");

    status.className =
        "trade-risk-loading";

    status.setAttribute(
        "role",
        "status"
    );

    status.textContent =
        `Building a risk plan for ${symbol}…`;

    panel.appendChild(status);
}


function renderRiskPlanError(
    message
) {
    const panel =
        document.getElementById(
            "trade-risk-plan"
        );

    if (!panel) {
        return;
    }

    panel.replaceChildren();

    const status =
        document.createElement("p");

    status.className =
        "trade-risk-error";

    status.setAttribute(
        "role",
        "alert"
    );

    status.textContent =
        message;

    panel.appendChild(status);
}


function applySuggestedShares(plan) {
    if (
        !plan ||
        plan.suggestedShares === null
    ) {
        return;
    }

    const sharesInput =
        document.getElementById(
            "shares"
        );

    if (
        !sharesInput ||
        String(
            sharesInput.value
        ).trim()
    ) {
        return;
    }

    sharesInput.value =
        String(
            plan.suggestedShares
        );

    updateTradePreview();
}


// ==========================================
// UI creation
// ==========================================

function createTradeCenterPanels() {
    const tradeForm =
        findTradeFormContainer();

    if (!tradeForm) {
        return;
    }

    if (
        !document.getElementById(
            "trade-risk-plan"
        )
    ) {
        const riskPanel =
            document.createElement(
                "section"
            );

        riskPanel.id =
            "trade-risk-plan";

        riskPanel.className =
            "trade-risk-panel";

        riskPanel.setAttribute(
            "aria-live",
            "polite"
        );

        tradeForm.appendChild(
            riskPanel
        );
    }

    if (
        !document.getElementById(
            "trade-preview"
        )
    ) {
        const preview =
            document.createElement(
                "section"
            );

        preview.id =
            "trade-preview";

        preview.className =
            "trade-preview-panel";

        preview.setAttribute(
            "aria-live",
            "polite"
        );

        tradeForm.appendChild(
            preview
        );
    }

    renderRiskPlan(null);
    updateTradePreview();
}


function findTradeFormContainer() {
    const symbolInput =
        document.getElementById(
            "symbol"
        );

    return (
        document.getElementById(
            "trade-center"
        ) ||
        document.getElementById(
            "trade-form"
        ) ||
        symbolInput?.closest("form") ||
        symbolInput?.parentElement
            ?.parentElement ||
        null
    );
}


function createTradeMetric(
    label,
    value
) {
    const metric =
        document.createElement("div");

    metric.className =
        "trade-metric";

    const labelElement =
        document.createElement("span");

    labelElement.className =
        "trade-metric-label";

    labelElement.textContent =
        label;

    const valueElement =
        document.createElement("strong");

    valueElement.className =
        "trade-metric-value";

    valueElement.textContent =
        value;

    metric.append(
        labelElement,
        valueElement
    );

    return metric;
}


// ==========================================
// Button behavior
// ==========================================

function bindTradeButtons() {
    const buyButtons =
        findTradeButtons("buy");

    const sellButtons =
        findTradeButtons("sell");

    buyButtons.forEach(button => {
        if (
            button.dataset.tradeBound ===
            "true"
        ) {
            return;
        }

        button.dataset.tradeBound =
            "true";

        if (
            !button.hasAttribute(
                "onclick"
            )
        ) {
            button.addEventListener(
                "click",
                buyStock
            );
        }
    });

    sellButtons.forEach(button => {
        if (
            button.dataset.tradeBound ===
            "true"
        ) {
            return;
        }

        button.dataset.tradeBound =
            "true";

        if (
            !button.hasAttribute(
                "onclick"
            )
        ) {
            button.addEventListener(
                "click",
                sellStock
            );
        }
    });
}


function setTradeButtonsDisabled(
    disabled,
    activeAction = null
) {
    const buyButtons =
        findTradeButtons("buy");

    const sellButtons =
        findTradeButtons("sell");

    updateTradeButtonGroup(
        buyButtons,
        disabled,
        activeAction === "buy"
            ? "Buying…"
            : null
    );

    updateTradeButtonGroup(
        sellButtons,
        disabled,
        activeAction === "sell"
            ? "Selling…"
            : null
    );
}


function updateTradeButtonGroup(
    buttons,
    disabled,
    loadingText
) {
    buttons.forEach(button => {
        if (
            !button.dataset.originalText
        ) {
            button.dataset.originalText =
                button.textContent.trim();
        }

        button.disabled =
            disabled;

        button.setAttribute(
            "aria-busy",
            String(
                disabled &&
                Boolean(
                    loadingText
                )
            )
        );

        button.textContent =
            loadingText ??
            button.dataset.originalText;
    });
}


function findTradeButtons(action) {
    const capitalizedAction =
        action.charAt(0).toUpperCase() +
        action.slice(1);

    const selectors = [
        `[data-trade-action="${action}"]`,
        `#${action}-button`,
        `#${action}Button`,
        `button[name="${action}"]`,
        `button[onclick="${action}Stock()"]`,
        `button[onclick="${action}Stock();"]`,
        `button[onclick="window.${action}Stock()"]`,
        `button[aria-label="${capitalizedAction} stock"]`
    ];

    return Array.from(
        document.querySelectorAll(
            selectors.join(",")
        )
    );
}


// ==========================================
// Messages
// ==========================================

function showTradeMessage(
    text,
    type = ""
) {
    const message =
        document.getElementById(
            "trade-message"
        );

    if (!message) {
        return;
    }

    message.textContent =
        String(text ?? "");

    message.classList.remove(
        "success",
        "error"
    );

    message.removeAttribute(
        "role"
    );

    if (type) {
        message.classList.add(type);
    }

    if (type === "error") {
        message.setAttribute(
            "role",
            "alert"
        );
    } else {
        message.setAttribute(
            "role",
            "status"
        );
    }
}


function showTradeSuccess(
    execution
) {
    const actionLabel =
        execution.action === "SELL"
            ? "Sold"
            : "Bought";

    const details = [
        `${actionLabel} ${execution.shares} share${execution.shares === 1 ? "" : "s"} of ${execution.symbol}`
    ];

    if (execution.price !== null) {
        details.push(
            `at ${formatTradeCurrency(
                execution.price
            )}`
        );
    }

    if (execution.total !== null) {
        details.push(
            `for ${formatTradeCurrency(
                execution.total
            )}`
        );
    }

    let message =
        `${details.join(" ")}.`;

    if (execution.message) {
        message =
            execution.message;
    }

    showTradeMessage(
        message,
        "success"
    );
}


function clearTradeMessageIfError() {
    const message =
        document.getElementById(
            "trade-message"
        );

    if (
        !message ||
        !message.classList.contains(
            "error"
        )
    ) {
        return;
    }

    message.textContent = "";
    message.classList.remove(
        "error"
    );

    message.removeAttribute(
        "role"
    );
}


// ==========================================
// Field states
// ==========================================

function setTradePriceLoading() {
    const priceInput =
        document.getElementById(
            "price"
        );

    if (!priceInput) {
        return;
    }

    priceInput.value = "";
    priceInput.placeholder =
        "Loading price…";

    priceInput.classList.remove(
        "error"
    );

    priceInput.setAttribute(
        "aria-busy",
        "true"
    );
}


function setTradePriceError(
    message
) {
    const priceInput =
        document.getElementById(
            "price"
        );

    currentTradeQuote = null;

    if (priceInput) {
        priceInput.value = "";
        priceInput.placeholder =
            "Price unavailable";

        priceInput.classList.add(
            "error"
        );

        priceInput.removeAttribute(
            "aria-busy"
        );
    }

    showTradeMessage(
        message,
        "error"
    );

    updateTradePreview();
}


function clearTradePrice() {
    const priceInput =
        document.getElementById(
            "price"
        );

    currentTradeQuote = null;

    if (!priceInput) {
        return;
    }

    priceInput.value = "";
    priceInput.placeholder =
        "0.00";

    priceInput.classList.remove(
        "error"
    );

    priceInput.removeAttribute(
        "aria-busy"
    );
}


function clearSharesInput() {
    const sharesInput =
        document.getElementById(
            "shares"
        );

    if (sharesInput) {
        sharesInput.value = "";
    }

    updateTradePreview();
}


function focusInvalidTradeField(
    symbol,
    shares
) {
    if (!symbol) {
        document
            .getElementById("symbol")
            ?.focus();

        return;
    }

    if (
        shares === null ||
        shares <= 0 ||
        !Number.isInteger(shares)
    ) {
        document
            .getElementById("shares")
            ?.focus();
    }
}


// ==========================================
// Request helpers
// ==========================================

function cancelactiveTradeQuoteRequest() {
    if (activeTradeQuoteRequest) {
        activeTradeQuoteRequest.abort();
        activeTradeQuoteRequest = null;
    }
}


function cancelActiveRiskRequest() {
    if (activeRiskRequest) {
        activeRiskRequest.abort();
        activeRiskRequest = null;
    }
}


function cancelActiveTradeRequest() {
    if (activeTradeRequest) {
        activeTradeRequest.abort();
        activeTradeRequest = null;
    }
}


async function readTradeJson(
    response
) {
    try {
        return await response.json();
    } catch {
        return null;
    }
}


function getTradeResponseMessage(
    payload,
    fallback
) {
    return String(
        payload?.detail ??
        payload?.message ??
        payload?.error ??
        fallback
    );
}


function getTradeApiUrl() {
    const apiUrl =
        String(
            window.API_URL ?? ""
        )
            .trim()
            .replace(/\/+$/, "");

    if (!apiUrl) {
        throw new Error(
            "API_URL is not configured."
        );
    }

    return apiUrl;
}


// ==========================================
// Data helpers
// ==========================================

function getTradeSymbol() {
    return normalizeTradeSymbol(
        document
            .getElementById("symbol")
            ?.value
    );
}


function getTradeShares() {
    const rawValue =
        document
            .getElementById("shares")
            ?.value;

    if (
        rawValue === null ||
        rawValue === undefined ||
        String(rawValue).trim() === ""
    ) {
        return null;
    }

    const shares =
        Number(rawValue);

    return Number.isInteger(shares)
        ? shares
        : null;
}


function getTradePrice() {
    return toPositiveTradeNumber(
        document
            .getElementById("price")
            ?.value
    );
}


function normalizeQuotePrice(
    payload
) {
    if (
        !payload ||
        typeof payload !== "object" ||
        Array.isArray(payload)
    ) {
        return null;
    }

    return firstPositiveTradeNumber(
        payload.price,
        payload.current_price,
        payload.last_price,
        payload.regularMarketPrice,
        payload.quote?.price
    );
}


function normalizeTradeSymbol(
    value
) {
    return String(value ?? "")
        .trim()
        .toUpperCase()
        .replace(
            /[^A-Z0-9.\-^]/g,
            ""
        )
        .slice(0, 20);
}


function normalizeTradeText(
    value
) {
    return String(value ?? "")
        .trim()
        .replace(/\s+/g, " ")
        .slice(0, 1_000);
}


function normalizeRiskLevel(
    value
) {
    const risk =
        normalizeTradeText(value)
            .toUpperCase();

    if (risk.includes("LOW")) {
        return "Low";
    }

    if (risk.includes("HIGH")) {
        return "High";
    }

    if (
        risk.includes("MEDIUM") ||
        risk.includes("MODERATE")
    ) {
        return "Medium";
    }

    return "Unknown";
}


function calculateRiskReward(
    entryPrice,
    stopLoss,
    takeProfit
) {
    if (
        entryPrice === null ||
        stopLoss === null ||
        takeProfit === null
    ) {
        return null;
    }

    const risk =
        entryPrice -
        stopLoss;

    const reward =
        takeProfit -
        entryPrice;

    if (
        risk <= 0 ||
        reward <= 0
    ) {
        return null;
    }

    return reward / risk;
}


function toPositiveTradeNumber(
    value
) {
    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return null;
    }

    const number =
        Number(value);

    return (
        Number.isFinite(number) &&
        number > 0
    )
        ? number
        : null;
}


function firstPositiveTradeNumber(
    ...values
) {
    for (const value of values) {
        const number =
            toPositiveTradeNumber(
                value
            );

        if (number !== null) {
            return number;
        }
    }

    return null;
}


function firstPositiveInteger(
    ...values
) {
    for (const value of values) {
        const number =
            Number(value);

        if (
            Number.isInteger(number) &&
            number > 0
        ) {
            return number;
        }
    }

    return null;
}


function firstFiniteTradeNumber(
    ...values
) {
    for (const value of values) {
        const number =
            Number(value);

        if (Number.isFinite(number)) {
            return number;
        }
    }

    return null;
}


function clampTradeNumber(
    value,
    minimum,
    maximum,
    fallback = null
) {
    const number =
        Number(value);

    if (!Number.isFinite(number)) {
        return fallback;
    }

    return Math.min(
        maximum,
        Math.max(
            minimum,
            number
        )
    );
}


// ==========================================
// Formatting
// ==========================================

function formatTradeCurrency(
    value
) {
    const number =
        Number(value);

    if (!Number.isFinite(number)) {
        return "—";
    }

    return tradeCurrencyFormatter.format(
        number
    );
}


function formatTradeNumber(
    value,
    decimalPlaces = 2
) {
    const number =
        Number(value);

    if (!Number.isFinite(number)) {
        return "—";
    }

    return number.toFixed(
        decimalPlaces
    );
}


// ==========================================
// Public API
// ==========================================

window.buyStock =
    buyStock;

window.sellStock =
    sellStock;

window.submitTrade =
    submitTrade;

window.loadLivePrice =
    loadLivePrice;

window.loadRiskPlan =
    loadRiskPlan;

window.refreshTradeCenter =
    async function refreshTradeCenter() {
        const symbol =
            getTradeSymbol();

        if (!symbol) {
            return;
        }

        await Promise.allSettled([
            loadLivePrice(symbol),
            loadRiskPlan(symbol)
        ]);
    };
})();
