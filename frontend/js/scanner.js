"use strict";

let scannerStocks = [];
let filteredScannerStocks = [];

let activeScannerRequest = null;
let latestScannerRequestNumber = 0;

let scannerResultsElement = null;
let scannerControlsElement = null;

const SCANNER_REQUEST_TIMEOUT_MS = 30_000;

const scannerState = {
    search: "",
    minimumScore: 0,
    rating: "ALL",
    risk: "ALL",
    trend: "ALL",
    sort: "SCORE_DESC",
};

const scannerCurrencyFormatter =
    new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });

const scannerPercentFormatter =
    new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
        signDisplay: "always",
    });


async function loadScanner() {
    scannerResultsElement =
        document.getElementById(
            "scanner-results"
        );

    if (!scannerResultsElement) {
        console.error(
            "Scanner results container was not found."
        );

        return;
    }

    createScannerControls();
    cancelActiveScannerRequest();

    const requestController =
        new AbortController();

    activeScannerRequest =
        requestController;

    const requestNumber =
        ++latestScannerRequestNumber;

    let requestTimedOut = false;

    const timeoutId =
        window.setTimeout(
            () => {
                requestTimedOut = true;
                requestController.abort();
            },
            SCANNER_REQUEST_TIMEOUT_MS
        );

    setScannerLoadingState();

    try {
        const response = await fetch(
            `${getScannerApiUrl()}/scanner`,
            {
                method: "GET",

                headers: {
                    Accept: "application/json",
                },

                signal:
                    requestController.signal,
            }
        );

        const payload =
            await readScannerJson(
                response
            );

        if (!response.ok) {
            const message =
                payload?.detail ??
                payload?.error ??
                `Scanner request failed with status ${response.status}.`;

            throw new Error(
                String(message)
            );
        }

        if (
            requestNumber !==
            latestScannerRequestNumber
        ) {
            return;
        }

        scannerStocks =
            normalizeScannerResponse(
                payload
            );

        updateScannerSummary();
        applyScannerFilters();
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            if (
                requestNumber !==
                latestScannerRequestNumber
            ) {
                return;
            }

            if (requestTimedOut) {
                showScannerStatus(
                    "The market scan took too long. Try refreshing the scanner.",
                    true
                );
            }

            return;
        }

        console.error(
            "Scanner request failed:",
            error
        );

        scannerStocks = [];
        filteredScannerStocks = [];

        updateScannerSummary();

        showScannerStatus(
            "Unable to load market scanner results.",
            true
        );
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeScannerRequest ===
            requestController
        ) {
            activeScannerRequest =
                null;
        }

        clearScannerLoadingState();
    }
}


function normalizeScannerResponse(
    payload
) {
    let stocks = payload;

    if (
        payload &&
        typeof payload ===
            "object" &&
        !Array.isArray(payload)
    ) {
        stocks =
            payload.stocks ??
            payload.results ??
            payload.opportunities ??
            payload.data ??
            [];
    }

    if (!Array.isArray(stocks)) {
        throw new Error(
            "The scanner response must contain an array of stocks."
        );
    }

    const normalizedStocks =
        stocks
            .map(
                normalizeScannerStock
            )
            .filter(Boolean);

    const uniqueStocks =
        new Map();

    for (
        const stock of
        normalizedStocks
    ) {
        const existing =
            uniqueStocks.get(
                stock.symbol
            );

        if (
            !existing ||
            stock.score >
                existing.score
        ) {
            uniqueStocks.set(
                stock.symbol,
                stock
            );
        }
    }

    return Array.from(
        uniqueStocks.values()
    );
}


function normalizeScannerStock(
    stock
) {
    if (
        !stock ||
        typeof stock !==
            "object"
    ) {
        return null;
    }

    const symbol =
        normalizeScannerSymbol(
            stock.symbol ??
            stock.ticker
        );

    if (!symbol) {
        return null;
    }

    const price =
        toFiniteScannerNumber(
            stock.price ??
            stock.current_price ??
            stock.last_price,
            0
        );

    const change =
        toFiniteScannerNumber(
            stock.change_percent ??
            stock.change ??
            stock.percent_change,
            0
        );

    const score =
        clampScannerNumber(
            stock.score ??
            stock.ai_score ??
            stock.rank_score,
            0,
            100
        );

    const confidence =
        clampScannerNumber(
            stock.confidence ??
            stock.ai_confidence ??
            score,
            0,
            100
        );

    const riskReward =
        Math.max(
            0,
            toFiniteScannerNumber(
                stock.risk_reward ??
                stock.risk_reward_ratio ??
                stock.rr_ratio,
                0
            )
        );

    const stopLoss =
        toNullableScannerNumber(
            stock.stop_loss ??
            stock.stop
        );

    const takeProfit =
        toNullableScannerNumber(
            stock.take_profit ??
            stock.target ??
            stock.price_target
        );

    const rating =
        normalizeScannerRating(
            stock.rating ??
            stock.recommendation ??
            getScannerRatingFromScore(
                score
            )
        );

    const risk =
        normalizeScannerRisk(
            stock.risk ??
            stock.risk_level
        );

    const trend =
        normalizeScannerTrend(
            stock.trend ??
            stock.trend_strength
        );

    return {
        symbol,

        company:
            normalizeScannerText(
                stock.company ??
                stock.company_name ??
                stock.name
            ),

        sector:
            normalizeScannerText(
                stock.sector
            ),

        price,
        change,
        score,
        confidence,
        rating,
        risk,
        trend,
        riskReward,
        stopLoss,
        takeProfit,

        signals:
            normalizeScannerSignals(
                stock.signals ??
                stock.reasons ??
                stock.indicators
            ),

        aiSummary:
            normalizeScannerText(
                stock.ai_summary ??
                stock.summary ??
                stock.analysis ??
                stock.explanation
            ),
    };
}
function applyScannerFilters() {
    const search =
        scannerState.search
            .trim()
            .toUpperCase();

    filteredScannerStocks =
        scannerStocks.filter(
            stock => {
                const matchesSearch =
                    !search ||
                    stock.symbol.includes(
                        search
                    ) ||
                    stock.company
                        .toUpperCase()
                        .includes(search) ||
                    stock.sector
                        .toUpperCase()
                        .includes(search);

                const matchesScore =
                    stock.score >=
                    scannerState.minimumScore;

                const matchesRating =
                    scannerState.rating ===
                        "ALL" ||
                    stock.rating ===
                        scannerState.rating;

                const matchesRisk =
                    scannerState.risk ===
                        "ALL" ||
                    stock.risk ===
                        scannerState.risk;

                const matchesTrend =
                    scannerState.trend ===
                        "ALL" ||
                    stock.trend ===
                        scannerState.trend;

                return (
                    matchesSearch &&
                    matchesScore &&
                    matchesRating &&
                    matchesRisk &&
                    matchesTrend
                );
            }
        );

    sortScannerStocks(
        filteredScannerStocks,
        scannerState.sort
    );

    renderScannerResults(
        filteredScannerStocks
    );

    updateScannerSummary();
}


function sortScannerStocks(
    stocks,
    sortMethod
) {
    const sorters = {
        SCORE_DESC:
            (first, second) =>
                second.score -
                first.score,

        CONFIDENCE_DESC:
            (first, second) =>
                second.confidence -
                first.confidence,

        CHANGE_DESC:
            (first, second) =>
                second.change -
                first.change,

        RISK_REWARD_DESC:
            (first, second) =>
                second.riskReward -
                first.riskReward,

        PRICE_ASC:
            (first, second) =>
                first.price -
                second.price,

        PRICE_DESC:
            (first, second) =>
                second.price -
                first.price,

        SYMBOL_ASC:
            (first, second) =>
                first.symbol.localeCompare(
                    second.symbol
                ),
    };

    stocks.sort(
        sorters[sortMethod] ??
        sorters.SCORE_DESC
    );
}


function renderScannerResults(
    stocks
) {
    if (!scannerResultsElement) {
        return;
    }

    scannerResultsElement.replaceChildren();

    if (stocks.length === 0) {
        const message =
            scannerStocks.length === 0
                ? "No market opportunities were found."
                : "No stocks match the current scanner filters.";

        showScannerStatus(
            message
        );

        return;
    }

    const fragment =
        document.createDocumentFragment();

    for (const stock of stocks) {
        fragment.appendChild(
            createScannerCard(stock)
        );
    }

    scannerResultsElement.appendChild(
        fragment
    );
}
function createScannerCard(stock) {
    const card = document.createElement("article");

    card.className = "scanner-card scanner-card-v3";
    card.dataset.symbol = stock.symbol;
    card.dataset.price = String(stock.price);
    card.tabIndex = 0;

    card.setAttribute("role", "button");
    card.setAttribute(
        "aria-label",
        `Select ${stock.symbol}, AI score ${Math.round(
            stock.score
        )} out of 100`
    );

    const header =
        document.createElement("div");

    header.className =
        "scanner-card-header";

    const identity =
        document.createElement("div");

    identity.className =
        "scanner-stock-identity";

    const symbol =
        document.createElement("strong");

    symbol.className =
        "scanner-symbol";

    symbol.textContent =
        stock.symbol;

    const company =
        document.createElement("span");

    company.className =
        "scanner-company";

    company.textContent =
        stock.company ||
        stock.sector ||
        "Market opportunity";

    const priceRow =
        document.createElement("div");

    priceRow.className =
        "scanner-price-row";

    const price =
        document.createElement("span");

    price.className =
        "scanner-price";

    price.textContent =
        formatScannerCurrency(
            stock.price
        );

    const change =
        document.createElement("span");

    change.className =
        stock.change >= 0
            ? "scanner-change positive"
            : "scanner-change negative";

    change.textContent =
        stock.change >= 0
            ? `▲ ${formatScannerPercent(
                stock.change
            )}%`
            : `▼ ${formatScannerPercent(
                stock.change
            )}%`;

    priceRow.append(
        price,
        change
    );

    identity.append(
        symbol,
        company,
        priceRow
    );

    const scoreBlock =
        document.createElement("div");

    scoreBlock.className =
        "scanner-score-block";

    const scoreLabel =
        document.createElement("span");

    scoreLabel.className =
        "scanner-score-label";

    scoreLabel.textContent =
        "AI Score";

    const scoreValue =
        document.createElement("strong");

    scoreValue.className =
        "scanner-score-value";

    scoreValue.textContent =
        Math.round(stock.score);

    const scoreMax =
        document.createElement("span");

    scoreMax.className =
        "scanner-score-max";

    scoreMax.textContent =
        "/100";

    scoreBlock.append(
        scoreLabel,
        scoreValue,
        scoreMax
    );

    header.append(
        identity,
        scoreBlock
    );

    const badges =
        document.createElement("div");

    badges.className =
        "scanner-badges";

    badges.append(
        createScannerBadge(
            stock.rating,
            `rating-${getScannerClassName(
                stock.rating
            )}`
        ),
        createScannerBadge(
            stock.trend,
            `trend-${getScannerClassName(
                stock.trend
            )}`
        ),
        createScannerBadge(
            `${stock.risk} RISK`,
            `risk-${getScannerClassName(
                stock.risk
            )}`
        )
    );

    const confidence =
        document.createElement("div");

    confidence.className =
        "scanner-confidence";

    const confidenceHeader =
        document.createElement("div");

    confidenceHeader.className =
        "scanner-confidence-header";

    const confidenceLabel =
        document.createElement("span");

    confidenceLabel.textContent =
        "AI Confidence";

    const confidenceValue =
        document.createElement("strong");

    confidenceValue.textContent =
        `${Math.round(
            stock.confidence
        )}%`;

    confidenceHeader.append(
        confidenceLabel,
        confidenceValue
    );

    const confidenceBar =
        document.createElement("div");

    confidenceBar.className =
        "confidence-bar";

    confidenceBar.setAttribute(
        "role",
        "progressbar"
    );

    confidenceBar.setAttribute(
        "aria-valuemin",
        "0"
    );

    confidenceBar.setAttribute(
        "aria-valuemax",
        "100"
    );

    confidenceBar.setAttribute(
        "aria-valuenow",
        String(
            Math.round(
                stock.confidence
            )
        )
    );

    const confidenceFill =
        document.createElement("div");

    confidenceFill.className =
        "confidence-bar-fill";

    confidenceFill.style.width =
        `${stock.confidence}%`;

    confidenceBar.appendChild(
        confidenceFill
    );

    confidence.append(
        confidenceHeader,
        confidenceBar
    );

    const metrics =
        document.createElement("div");

    metrics.className =
        "scanner-metrics";

    metrics.append(
        createScannerMetric(
            "Risk / Reward",
            stock.riskReward > 0
                ? `${formatScannerNumber(
                    stock.riskReward,
                    2
                )}:1`
                : "—"
        ),
        createScannerMetric(
            "Suggested Stop",
            stock.stopLoss !== null
                ? formatScannerCurrency(
                    stock.stopLoss
                )
                : "—"
        ),
        createScannerMetric(
            "Target",
            stock.takeProfit !== null
                ? formatScannerCurrency(
                    stock.takeProfit
                )
                : "—"
        )
    );

    card.append(
        header,
        badges,
        confidence,
        metrics
    );

    if (stock.signals.length > 0) {
        const signals =
            document.createElement(
                "section"
            );

        signals.className =
            "scanner-signals";

        const signalsTitle =
            document.createElement(
                "h4"
            );

        signalsTitle.textContent =
            "Why it scored";

        const signalList =
            document.createElement(
                "ul"
            );

        for (
            const signal of
            stock.signals.slice(0, 5)
        ) {
            const signalItem =
                document.createElement(
                    "li"
                );

            const icon =
                document.createElement(
                    "span"
                );

            icon.className =
                "scanner-signal-icon";

            icon.textContent =
                "✓";

            const text =
                document.createElement(
                    "span"
                );

            text.textContent =
                signal;

            signalItem.append(
                icon,
                text
            );

            signalList.appendChild(
                signalItem
            );
        }

        signals.append(
            signalsTitle,
            signalList
        );

        card.appendChild(
            signals
        );
    }

    if (stock.aiSummary) {
        const insight =
            document.createElement(
                "section"
            );

        insight.className =
            "scanner-ai-insight";

        const insightTitle =
            document.createElement(
                "h4"
            );

        insightTitle.textContent =
            "AI Insight";

        const insightText =
            document.createElement(
                "p"
            );

        insightText.textContent =
            stock.aiSummary;

        insight.append(
            insightTitle,
            insightText
        );

        card.appendChild(
            insight
        );
    }

    const actions =
        document.createElement("div");

    actions.className =
        "scanner-card-actions";

    const analyzeButton =
        document.createElement(
            "button"
        );

    analyzeButton.type =
        "button";

    analyzeButton.className =
        "scanner-action-button scanner-analyze-button";

    analyzeButton.dataset.action =
        "analyze";

    analyzeButton.textContent =
        "Analyze";

    const tradeButton =
        document.createElement(
            "button"
        );

    tradeButton.type =
        "button";

    tradeButton.className =
        "scanner-action-button scanner-trade-button";

    tradeButton.dataset.action =
        "trade";

    tradeButton.textContent =
        "Select Trade";

    actions.append(
        analyzeButton,
        tradeButton
    );

    card.appendChild(
        actions
    );

    return card;
}


function createScannerBadge(
    text,
    typeClass
) {
    const badge =
        document.createElement(
            "span"
        );

    badge.className =
        `scanner-v3-badge ${typeClass}`;

    badge.textContent =
        text;

    return badge;
}


function createScannerMetric(
    label,
    value
) {
    const metric =
        document.createElement(
            "div"
        );

    metric.className =
        "scanner-metric";

    const labelElement =
        document.createElement(
            "span"
        );

    labelElement.className =
        "scanner-metric-label";

    labelElement.textContent =
        label;

    const valueElement =
        document.createElement(
            "strong"
        );

    valueElement.className =
        "scanner-metric-value";

    valueElement.textContent =
        value;

    metric.append(
        labelElement,
        valueElement
    );

    return metric;
}
function createScannerControls() {
    if (scannerControlsElement) {
        return;
    }

    scannerResultsElement =
        scannerResultsElement ??
        document.getElementById(
            "scanner-results"
        );

    if (!scannerResultsElement) {
        return;
    }

    const existingSearchInput =
        document.getElementById(
            "scanner-search"
        );

    const existingSortSelect =
        document.getElementById(
            "scanner-sort"
        );

    if (
        existingSearchInput &&
        !existingSearchInput.dataset.scannerBound
    ) {
        existingSearchInput.dataset.scannerBound =
            "true";

        existingSearchInput.addEventListener(
            "input",
            event => {
                scannerState.search =
                    event.target.value;

                applyScannerFilters();
            }
        );
    }

    if (
        existingSortSelect &&
        !existingSortSelect.dataset.scannerBound
    ) {
        existingSortSelect.dataset.scannerBound =
            "true";

        existingSortSelect.addEventListener(
            "change",
            event => {
                scannerState.sort =
                    mapScannerSortValue(
                        event.target.value
                    );

                applyScannerFilters();
            }
        );
    }

    scannerControlsElement =
        document.getElementById(
            "scanner-controls"
        );

    if (!scannerControlsElement) {
        scannerControlsElement =
            document.createElement(
                "section"
            );

        scannerControlsElement.id =
            "scanner-controls";

        scannerControlsElement.className =
            "scanner-controls";

        scannerControlsElement.setAttribute(
            "aria-label",
            "Advanced scanner filters"
        );

        scannerResultsElement
            .parentElement
            ?.insertBefore(
                scannerControlsElement,
                scannerResultsElement
            );
    }

    scannerControlsElement.replaceChildren();

    const scoreSelect =
        createScannerSelect(
            "scanner-minimum-score",
            "Minimum score",
            [
                ["0", "Any score"],
                ["70", "70+"],
                ["80", "80+"],
                ["90", "90+"],
                ["95", "95+"],
            ]
        );

    scoreSelect.addEventListener(
        "change",
        event => {
            scannerState.minimumScore =
                Number(
                    event.target.value
                );

            applyScannerFilters();
        }
    );

    const ratingSelect =
        createScannerSelect(
            "scanner-rating-filter",
            "Rating",
            [
                ["ALL", "All ratings"],
                [
                    "STRONG BUY",
                    "Strong Buy",
                ],
                ["BUY", "Buy"],
                ["WATCH", "Watch"],
                ["AVOID", "Avoid"],
                [
                    "STRONG SELL",
                    "Strong Sell",
                ],
            ]
        );

    ratingSelect.addEventListener(
        "change",
        event => {
            scannerState.rating =
                event.target.value;

            applyScannerFilters();
        }
    );

    const riskSelect =
        createScannerSelect(
            "scanner-risk-filter",
            "Risk",
            [
                ["ALL", "All risk levels"],
                ["LOW", "Low"],
                ["MEDIUM", "Medium"],
                ["HIGH", "High"],
                ["UNKNOWN", "Unknown"],
            ]
        );

    riskSelect.addEventListener(
        "change",
        event => {
            scannerState.risk =
                event.target.value;

            applyScannerFilters();
        }
    );

    const trendSelect =
        createScannerSelect(
            "scanner-trend-filter",
            "Trend",
            [
                ["ALL", "All trends"],
                [
                    "STRONG BULLISH",
                    "Strong Bullish",
                ],
                ["BULLISH", "Bullish"],
                ["NEUTRAL", "Neutral"],
                ["BEARISH", "Bearish"],
                [
                    "STRONG BEARISH",
                    "Strong Bearish",
                ],
            ]
        );

    trendSelect.addEventListener(
        "change",
        event => {
            scannerState.trend =
                event.target.value;

            applyScannerFilters();
        }
    );

    const refreshButton =
        document.createElement(
            "button"
        );

    refreshButton.type =
        "button";

    refreshButton.className =
        "scanner-refresh-button secondary-button";

    refreshButton.textContent =
        "Refresh Scan";

    refreshButton.addEventListener(
        "click",
        loadScanner
    );

    const summary =
        document.createElement(
            "div"
        );

    summary.id =
        "scanner-summary";

    summary.className =
        "scanner-summary";

    summary.setAttribute(
        "aria-live",
        "polite"
    );

    scannerControlsElement.append(
        scoreSelect,
        ratingSelect,
        riskSelect,
        trendSelect,
        refreshButton,
        summary
    );
}


function createScannerSelect(
    id,
    label,
    options
) {
    const wrapper =
        document.createElement(
            "label"
        );

    wrapper.className =
        "scanner-filter-field";

    wrapper.htmlFor =
        id;

    const labelText =
        document.createElement(
            "span"
        );

    labelText.textContent =
        label;

    const select =
        document.createElement(
            "select"
        );

    select.id =
        id;

    select.name =
        id;

    select.className =
        "scanner-filter-select";

    for (
        const [
            value,
            text,
        ] of options
    ) {
        const option =
            document.createElement(
                "option"
            );

        option.value =
            value;

        option.textContent =
            text;

        select.appendChild(
            option
        );
    }

    wrapper.append(
        labelText,
        select
    );

    return wrapper;
}


function mapScannerSortValue(
    value
) {
    const sortValues = {
        "score-desc":
            "SCORE_DESC",

        "confidence-desc":
            "CONFIDENCE_DESC",

        "symbol-asc":
            "SYMBOL_ASC",

        "price-desc":
            "PRICE_DESC",

        SCORE_DESC:
            "SCORE_DESC",

        CONFIDENCE_DESC:
            "CONFIDENCE_DESC",

        CHANGE_DESC:
            "CHANGE_DESC",

        RISK_REWARD_DESC:
            "RISK_REWARD_DESC",

        PRICE_ASC:
            "PRICE_ASC",

        PRICE_DESC:
            "PRICE_DESC",

        SYMBOL_ASC:
            "SYMBOL_ASC",
    };

    return sortValues[value] ??
        "SCORE_DESC";
}


function handleScannerClick(
    event
) {
    const card =
        event.target.closest(
            ".scanner-card"
        );

    if (!card) {
        return;
    }

    const stock =
        getScannerStockFromCard(
            card
        );

    if (!stock) {
        return;
    }

    const action =
        event.target.closest(
            "[data-action]"
        )?.dataset.action;

    if (
        action === "analyze"
    ) {
        selectStock(
            stock.symbol,
            stock.price,
            false
        );

        return;
    }

    selectStock(
        stock.symbol,
        stock.price,
        true
    );
}


function handleScannerKeydown(
    event
) {
    if (
        event.key !== "Enter" &&
        event.key !== " "
    ) {
        return;
    }

    if (
        event.target.closest(
            "button, input, select"
        )
    ) {
        return;
    }

    const card =
        event.target.closest(
            ".scanner-card"
        );

    if (!card) {
        return;
    }

    event.preventDefault();

    const stock =
        getScannerStockFromCard(
            card
        );

    if (stock) {
        selectStock(
            stock.symbol,
            stock.price,
            true
        );
    }
}


function getScannerStockFromCard(
    card
) {
    const symbol =
        normalizeScannerSymbol(
            card.dataset.symbol
        );

    return scannerStocks.find(
        stock =>
            stock.symbol ===
            symbol
    );
}


function selectStock(
    symbol,
    price,
    focusTradeForm = true
) {
    const cleanSymbol =
        normalizeScannerSymbol(
            symbol
        );

    const cleanPrice =
        toFiniteScannerNumber(
            price,
            0
        );

    if (!cleanSymbol) {
        return;
    }

    const symbolInput =
        document.getElementById(
            "symbol"
        );

    const priceInput =
        document.getElementById(
            "price"
        );

    if (symbolInput) {
        symbolInput.value =
            cleanSymbol;

        symbolInput.dispatchEvent(
            new Event(
                "input",
                {
                    bubbles: true,
                }
            )
        );

        symbolInput.dispatchEvent(
            new Event(
                "change",
                {
                    bubbles: true,
                }
            )
        );
    }

    if (
        priceInput &&
        cleanPrice > 0
    ) {
        priceInput.value =
            cleanPrice.toFixed(2);

        priceInput.dispatchEvent(
            new Event(
                "change",
                {
                    bubbles: true,
                }
            )
        );
    }

    if (
        typeof window.loadChart ===
        "function"
    ) {
        window.loadChart(
            cleanSymbol
        );
    }

    if (
        focusTradeForm &&
        symbolInput
    ) {
        symbolInput.scrollIntoView({
            behavior:
                "smooth",

            block:
                "center",
        });

        window.setTimeout(
            () => {
                symbolInput.focus();
            },
            350
        );
    }

    document.dispatchEvent(
        new CustomEvent(
            "scanner:stock-selected",
            {
                detail: {
                    symbol:
                        cleanSymbol,

                    price:
                        cleanPrice,
                },
            }
        )
    );
}
function setScannerLoadingState() {
    if (!scannerResultsElement) {
        return;
    }

    scannerResultsElement.setAttribute(
        "aria-busy",
        "true"
    );

    showScannerStatus(
        "Scanning the market for opportunities…"
    );
}


function clearScannerLoadingState() {
    scannerResultsElement
        ?.removeAttribute(
            "aria-busy"
        );
}


function showScannerStatus(
    message,
    isError = false
) {
    if (!scannerResultsElement) {
        return;
    }

    scannerResultsElement.replaceChildren();

    const status =
        document.createElement(
            "div"
        );

    status.className =
        "scanner-status-message";

    if (isError) {
        status.classList.add(
            "scanner-error-message"
        );
    }

    status.setAttribute(
        "role",
        isError
            ? "alert"
            : "status"
    );

    status.textContent =
        message;

    scannerResultsElement.appendChild(
        status
    );
}


function updateScannerSummary() {
    const summary =
        document.getElementById(
            "scanner-summary"
        );

    if (!summary) {
        return;
    }

    if (
        scannerStocks.length === 0
    ) {
        summary.textContent =
            "No scanner results";

        return;
    }

    summary.textContent =
        `${filteredScannerStocks.length} of ${scannerStocks.length} opportunities shown`;
}


function cancelActiveScannerRequest() {
    if (!activeScannerRequest) {
        return;
    }

    activeScannerRequest.abort();
    activeScannerRequest = null;
}


async function readScannerJson(
    response
) {
    try {
        return await response.json();
    } catch {
        return null;
    }
}


function getScannerApiUrl() {
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


function normalizeScannerSignals(
    signals
) {
    if (!Array.isArray(signals)) {
        return [];
    }

    return Array.from(
        new Set(
            signals
                .map(
                    normalizeScannerText
                )
                .filter(Boolean)
        )
    );
}


function normalizeScannerRating(
    value
) {
    const rating =
        normalizeScannerText(value)
            .toUpperCase()
            .replace(
                /[_-]+/g,
                " "
            );

    const ratings = {
        "STRONG BUY":
            "STRONG BUY",

        BUY:
            "BUY",

        HOLD:
            "WATCH",

        WATCH:
            "WATCH",

        NEUTRAL:
            "WATCH",

        AVOID:
            "AVOID",

        SELL:
            "AVOID",

        "STRONG SELL":
            "STRONG SELL",
    };

    return ratings[rating] ??
        "WATCH";
}


function normalizeScannerRisk(
    value
) {
    const risk =
        normalizeScannerText(value)
            .toUpperCase();

    if (
        risk.includes("LOW")
    ) {
        return "LOW";
    }

    if (
        risk.includes("HIGH")
    ) {
        return "HIGH";
    }

    if (
        risk.includes("MEDIUM") ||
        risk.includes("MODERATE")
    ) {
        return "MEDIUM";
    }

    return "UNKNOWN";
}


function normalizeScannerTrend(
    value
) {
    const trend =
        normalizeScannerText(value)
            .toUpperCase()
            .replace(
                /[_-]+/g,
                " "
            );

    const trends = {
        "STRONG BULLISH":
            "STRONG BULLISH",

        BULLISH:
            "BULLISH",

        NEUTRAL:
            "NEUTRAL",

        BEARISH:
            "BEARISH",

        "STRONG BEARISH":
            "STRONG BEARISH",
    };

    return trends[trend] ??
        "NEUTRAL";
}


function getScannerRatingFromScore(
    score
) {
    if (score >= 90) {
        return "STRONG BUY";
    }

    if (score >= 75) {
        return "BUY";
    }

    if (score >= 55) {
        return "WATCH";
    }

    if (score >= 35) {
        return "AVOID";
    }

    return "STRONG SELL";
}


function normalizeScannerSymbol(
    symbol
) {
    return String(
        symbol ?? ""
    )
        .trim()
        .toUpperCase()
        .replace(
            /[^A-Z0-9.\-^]/g,
            ""
        )
        .slice(
            0,
            20
        );
}


function normalizeScannerText(
    value
) {
    return String(
        value ?? ""
    )
        .trim()
        .replace(
            /\s+/g,
            " "
        )
        .slice(
            0,
            500
        );
}


function getScannerClassName(
    value
) {
    return String(
        value ?? ""
    )
        .toLowerCase()
        .replace(
            /[^a-z0-9]+/g,
            "-"
        )
        .replace(
            /^-+|-+$/g,
            ""
        );
}


function toFiniteScannerNumber(
    value,
    fallback = 0
) {
    const number =
        Number(value);

    return Number.isFinite(number)
        ? number
        : fallback;
}


function toNullableScannerNumber(
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

    return Number.isFinite(number)
        ? number
        : null;
}


function clampScannerNumber(
    value,
    minimum,
    maximum
) {
    const number =
        toFiniteScannerNumber(
            value,
            minimum
        );

    return Math.min(
        maximum,
        Math.max(
            minimum,
            number
        )
    );
}


function formatScannerCurrency(
    value
) {
    const number =
        Number(value);

    if (!Number.isFinite(number)) {
        return "—";
    }

    return scannerCurrencyFormatter.format(
        number
    );
}


function formatScannerPercent(
    value
) {
    const number =
        Number(value);

    if (!Number.isFinite(number)) {
        return "0.00";
    }

    return scannerPercentFormatter.format(
        number
    );
}


function formatScannerNumber(
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


window.loadScanner =
    loadScanner;

window.selectStock =
    selectStock;

window.refreshScanner =
    loadScanner;


document.addEventListener(
    "DOMContentLoaded",
    () => {
        scannerResultsElement =
            document.getElementById(
                "scanner-results"
            );

        if (!scannerResultsElement) {
            return;
        }

        scannerResultsElement.addEventListener(
            "click",
            handleScannerClick
        );

        scannerResultsElement.addEventListener(
            "keydown",
            handleScannerKeydown
        );

        loadScanner();
    }
);