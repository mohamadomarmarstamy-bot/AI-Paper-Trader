"use strict";

// =========================================================
// AI Research Lab
// =========================================================

(() => {
    const DEFAULT_API_URL = "http://127.0.0.1:8000";
    const REQUEST_TIMEOUT_MS = 30_000;

    let activeController = null;
    let activeRequestId = 0;
    let currentResults = [];

    // =====================================================
    // Configuration
    // =====================================================

    function getApiUrl() {
        return String(window.API_URL || DEFAULT_API_URL).replace(/\/+$/, "");
    }

    // =====================================================
    // General helpers
    // =====================================================

    function normalizeSymbol(value) {
        return String(value ?? "")
            .trim()
            .toUpperCase()
            .replace(/[^A-Z0-9.-]/g, "");
    }

    function getWatchlistSymbols() {
        const source = Array.isArray(window.watchlist)
            ? window.watchlist
            : readStoredWatchlist();

        return [
            ...new Set(
                source
                    .map(normalizeSymbol)
                    .filter(Boolean)
            )
        ];
    }

    function readStoredWatchlist() {
        try {
            const storedValue = localStorage.getItem("watchlist");

            if (!storedValue) {
                return [];
            }

            const parsedValue = JSON.parse(storedValue);
            return Array.isArray(parsedValue) ? parsedValue : [];
        } catch (error) {
            console.warn("Could not read the saved watchlist:", error);
            return [];
        }
    }

    function createElement(tagName, options = {}) {
        const element = document.createElement(tagName);

        if (options.className) {
            element.className = options.className;
        }

        if (options.text !== undefined) {
            element.textContent = String(options.text);
        }

        if (options.type) {
            element.type = options.type;
        }

        if (options.attributes) {
            Object.entries(options.attributes).forEach(([name, value]) => {
                if (value !== null && value !== undefined) {
                    element.setAttribute(name, String(value));
                }
            });
        }

        return element;
    }

    function toFiniteNumber(value) {
        const numericValue = Number(value);
        return Number.isFinite(numericValue) ? numericValue : null;
    }

    function clamp(value, minimum, maximum) {
        return Math.min(Math.max(value, minimum), maximum);
    }

    function normalizePercentage(value) {
        const numericValue = toFiniteNumber(value);

        if (numericValue === null) {
            return null;
        }

        if (numericValue >= 0 && numericValue <= 1) {
            return numericValue * 100;
        }

        return clamp(numericValue, 0, 100);
    }

    function formatCurrency(value) {
        const numericValue = toFiniteNumber(value);

        if (numericValue === null) {
            return "—";
        }

        return new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: "USD",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(numericValue);
    }

    function formatPercentage(value) {
        const numericValue = normalizePercentage(value);

        if (numericValue === null) {
            return "—";
        }

        return `${Math.round(numericValue)}%`;
    }

    function formatScore(value) {
        const numericValue = toFiniteNumber(value);

        if (numericValue === null) {
            return "—";
        }

        return Math.round(numericValue).toString();
    }

    function formatRatio(value) {
        const numericValue = toFiniteNumber(value);

        if (numericValue === null) {
            return "—";
        }

        return `${numericValue.toFixed(2)}:1`;
    }

    function getFirstDefined(object, keys, fallback = null) {
        for (const key of keys) {
            const value = object?.[key];

            if (value !== null && value !== undefined && value !== "") {
                return value;
            }
        }

        return fallback;
    }

    function normalizeLabel(value, fallback = "Unknown") {
        const text = String(value ?? "").trim();

        if (!text) {
            return fallback;
        }

        return text
            .replaceAll("_", " ")
            .replaceAll("-", " ")
            .toLowerCase()
            .replace(/\b\w/g, (character) => character.toUpperCase());
    }

    function normalizeClassToken(value) {
        return String(value ?? "unknown")
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "") || "unknown";
    }

    function createStatusBadge(value, category) {
        const label = normalizeLabel(value);
        const token = normalizeClassToken(value);

        return createElement("span", {
            className: `badge ${category}-badge ${category}-${token}`,
            text: label
        });
    }

    // =====================================================
    // Backend response normalization
    // =====================================================

    function normalizeReasons(data) {
        const source = getFirstDefined(
            data,
            ["reasons", "reason", "signals", "strengths"],
            []
        );

        if (Array.isArray(source)) {
            return source
                .map((item) => {
                    if (typeof item === "string") {
                        return item.trim();
                    }

                    if (item && typeof item === "object") {
                        return String(
                            item.message ??
                            item.reason ??
                            item.signal ??
                            item.name ??
                            ""
                        ).trim();
                    }

                    return "";
                })
                .filter(Boolean);
        }

        if (typeof source === "string" && source.trim()) {
            return [source.trim()];
        }

        return [];
    }

    function normalizeResearchResult(requestedSymbol, data) {
        const signal = getFirstDefined(
            data,
            ["signal", "recommendation", "action"],
            "UNKNOWN"
        );

        const rating = getFirstDefined(
            data,
            ["rating", "ai_rating"],
            signal
        );

        const confidence = getFirstDefined(
            data,
            ["confidence", "confidence_score", "probability"]
        );

        const score = getFirstDefined(
            data,
            ["score", "ai_score", "strategy_score"]
        );

        return {
            symbol: normalizeSymbol(data.symbol || requestedSymbol),
            company: String(
                getFirstDefined(data, ["company", "name", "company_name"], "")
            ).trim(),
            price: getFirstDefined(
                data,
                ["price", "current_price", "market_price"]
            ),
            signal,
            rating,
            confidence,
            score,
            trend: getFirstDefined(
                data,
                ["trend", "trend_direction", "market_trend"],
                "Unknown"
            ),
            risk: getFirstDefined(
                data,
                ["risk", "risk_level", "risk_rating"],
                "Unknown"
            ),
            stopLoss: getFirstDefined(
                data,
                ["stop_loss", "stopLoss"]
            ),
            takeProfit: getFirstDefined(
                data,
                ["take_profit", "takeProfit", "target_price"]
            ),
            riskReward: getFirstDefined(
                data,
                ["risk_reward", "risk_reward_ratio", "riskReward"]
            ),
            summary: String(
                getFirstDefined(
                    data,
                    ["summary", "ai_summary", "analysis"],
                    ""
                )
            ).trim(),
            reasons: normalizeReasons(data),
            raw: data
        };
    }

    // =====================================================
    // API
    // =====================================================

    async function fetchStrategy(symbol, signal) {
        const encodedSymbol = encodeURIComponent(symbol);
        const timeoutController = new AbortController();

        const timeoutId = window.setTimeout(() => {
            timeoutController.abort();
        }, REQUEST_TIMEOUT_MS);

        const combinedController = new AbortController();

        const abortCombinedRequest = () => {
            combinedController.abort();
        };

        signal?.addEventListener("abort", abortCombinedRequest, {
            once: true
        });

        timeoutController.signal.addEventListener(
            "abort",
            abortCombinedRequest,
            { once: true }
        );

        try {
            const response = await fetch(
                `${getApiUrl()}/strategy/${encodedSymbol}`,
                {
                    method: "GET",
                    headers: {
                        Accept: "application/json"
                    },
                    signal: combinedController.signal
                }
            );

            let data = null;

            try {
                data = await response.json();
            } catch {
                // The response may not contain valid JSON.
            }

            if (!response.ok) {
                const detail =
                    data?.detail ||
                    data?.error ||
                    `Backend returned HTTP ${response.status}.`;

                throw new Error(
                    typeof detail === "string"
                        ? detail
                        : "Strategy analysis failed."
                );
            }

            if (!data || typeof data !== "object" || Array.isArray(data)) {
                throw new Error("The backend returned an invalid response.");
            }

            if (data.error) {
                throw new Error(String(data.error));
            }

            return normalizeResearchResult(symbol, data);
        } catch (error) {
            if (combinedController.signal.aborted) {
                if (signal?.aborted) {
                    throw new DOMException(
                        "The request was cancelled.",
                        "AbortError"
                    );
                }

                throw new Error(
                    `Analysis for ${symbol} timed out after ${
                        REQUEST_TIMEOUT_MS / 1000
                    } seconds.`
                );
            }

            if (error instanceof TypeError) {
                throw new Error(
                    "Could not connect to the backend. Check the API URL, FastAPI server, and CORS settings."
                );
            }

            throw error;
        } finally {
            window.clearTimeout(timeoutId);

            signal?.removeEventListener(
                "abort",
                abortCombinedRequest
            );
        }
    }

    // =====================================================
    // Table helpers
    // =====================================================

    function getStrategyTableBody() {
        const target = document.getElementById("strategy-table");

        if (!target) {
            return null;
        }

        if (target.tagName === "TBODY") {
            return target;
        }

        if (target.tagName === "TABLE") {
            return target.tBodies[0] || target.createTBody();
        }

        return target;
    }

    function getColumnCount() {
        const tableBody = getStrategyTableBody();
        const table = tableBody?.closest("table");
        const headerCells = table?.querySelectorAll("thead th");

        return Math.max(headerCells?.length || 5, 1);
    }

    function clearTable() {
        const tableBody = getStrategyTableBody();

        if (tableBody) {
            tableBody.replaceChildren();
        }
    }

    function createMessageRow(message, className = "") {
        const row = createElement("tr", {
            className
        });

        const cell = createElement("td", {
            text: message,
            attributes: {
                colspan: getColumnCount()
            }
        });

        row.appendChild(cell);
        return row;
    }

    function showTableMessage(message, className = "") {
        const tableBody = getStrategyTableBody();

        if (!tableBody) {
            return;
        }

        tableBody.replaceChildren(
            createMessageRow(message, className)
        );
    }

    function createSymbolButton(result) {
        const button = createElement("button", {
            className: "symbol-link",
            text: result.symbol,
            type: "button",
            attributes: {
                "aria-label": `Open ${result.symbol}`
            }
        });

        button.addEventListener("click", () => {
            if (typeof window.selectStock === "function") {
                window.selectStock(result.symbol);
            } else if (typeof window.selectWatchStock === "function") {
                window.selectWatchStock(result.symbol);
            }
        });

        return button;
    }

    function createReasonContent(result) {
        const container = createElement("div", {
            className: "strategy-reason"
        });

        if (result.summary) {
            container.appendChild(
                createElement("p", {
                    className: "strategy-summary",
                    text: result.summary
                })
            );
        }

        if (result.reasons.length > 0) {
            const list = createElement("ul", {
                className: "strategy-reason-list"
            });

            result.reasons.slice(0, 3).forEach((reason) => {
                list.appendChild(
                    createElement("li", {
                        text: reason
                    })
                );
            });

            container.appendChild(list);
        }

        if (!result.summary && result.reasons.length === 0) {
            container.appendChild(
                createElement("span", {
                    className: "text-muted",
                    text: "No explanation available."
                })
            );
        }

        return container;
    }

    function createConfidenceContent(value) {
        const container = createElement("div", {
            className: "strategy-confidence"
        });

        const percentage = normalizePercentage(value);

        container.appendChild(
            createElement("span", {
                className: "strategy-confidence-value",
                text: formatPercentage(value)
            })
        );

        if (percentage !== null) {
            const bar = createElement("div", {
                className: "confidence-bar",
                attributes: {
                    role: "progressbar",
                    "aria-valuemin": "0",
                    "aria-valuemax": "100",
                    "aria-valuenow": Math.round(percentage)
                }
            });

            const fill = createElement("div", {
                className: "confidence-bar-fill"
            });

            fill.style.width = `${percentage}%`;
            bar.appendChild(fill);
            container.appendChild(bar);
        }

        return container;
    }

    function createSuccessRow(result) {
        const row = createElement("tr", {
            className: "strategy-result-row",
            attributes: {
                "data-symbol": result.symbol,
                "data-signal": normalizeClassToken(result.signal),
                "data-rating": normalizeClassToken(result.rating),
                "data-trend": normalizeClassToken(result.trend),
                "data-risk": normalizeClassToken(result.risk)
            }
        });

        const symbolCell = createElement("td");
        symbolCell.appendChild(createSymbolButton(result));

        if (result.company) {
            symbolCell.appendChild(
                createElement("div", {
                    className: "strategy-company text-muted",
                    text: result.company
                })
            );
        }

        const priceCell = createElement("td", {
            text: formatCurrency(result.price)
        });

        const signalCell = createElement("td");
        signalCell.appendChild(
            createStatusBadge(result.signal, "signal")
        );

        const confidenceCell = createElement("td");
        confidenceCell.appendChild(
            createConfidenceContent(result.confidence)
        );

        const reasonCell = createElement("td");
        reasonCell.appendChild(createReasonContent(result));

        row.append(
            symbolCell,
            priceCell,
            signalCell,
            confidenceCell,
            reasonCell
        );

        return row;
    }

    function createErrorRow(symbol, error) {
        const row = createElement("tr", {
            className: "strategy-error-row",
            attributes: {
                "data-symbol": symbol
            }
        });

        const symbolCell = createElement("td");
        symbolCell.appendChild(
            createElement("strong", {
                text: symbol
            })
        );

        const priceCell = createElement("td", {
            text: "—"
        });

        const signalCell = createElement("td");
        signalCell.appendChild(
            createStatusBadge("Unknown", "signal")
        );

        const confidenceCell = createElement("td", {
            text: "—"
        });

        const messageCell = createElement("td", {
            className: "text-danger",
            text: error?.message || "Analysis failed."
        });

        row.append(
            symbolCell,
            priceCell,
            signalCell,
            confidenceCell,
            messageCell
        );

        return row;
    }

    function createLoadingRow(symbol) {
        const row = createElement("tr", {
            className: "strategy-loading-row",
            attributes: {
                "data-symbol": symbol,
                "aria-busy": "true"
            }
        });

        const symbolCell = createElement("td", {
            text: symbol
        });

        const statusCell = createElement("td", {
            attributes: {
                colspan: Math.max(getColumnCount() - 1, 1)
            }
        });

        const status = createElement("div", {
            className: "strategy-loading-status"
        });

        status.append(
            createElement("span", {
                className: "loading-spinner",
                attributes: {
                    "aria-hidden": "true"
                }
            }),
            createElement("span", {
                text: `Analyzing ${symbol}…`
            })
        );

        statusCell.appendChild(status);
        row.append(symbolCell, statusCell);

        return row;
    }

    // =====================================================
    // Filtering and sorting
    // =====================================================

    function getFilterInput() {
        return (
            document.getElementById("strategy-filter") ||
            document.getElementById("research-filter")
        );
    }

    function getSortSelect() {
        return (
            document.getElementById("strategy-sort") ||
            document.getElementById("research-sort")
        );
    }

    function sortResults(results, sortValue) {
        const sortedResults = [...results];

        switch (sortValue) {
            case "symbol-desc":
                sortedResults.sort((a, b) =>
                    b.symbol.localeCompare(a.symbol)
                );
                break;

            case "confidence-desc":
                sortedResults.sort(
                    (a, b) =>
                        (normalizePercentage(b.confidence) ?? -1) -
                        (normalizePercentage(a.confidence) ?? -1)
                );
                break;

            case "confidence-asc":
                sortedResults.sort(
                    (a, b) =>
                        (normalizePercentage(a.confidence) ?? 101) -
                        (normalizePercentage(b.confidence) ?? 101)
                );
                break;

            case "score-desc":
                sortedResults.sort(
                    (a, b) =>
                        (toFiniteNumber(b.score) ?? -Infinity) -
                        (toFiniteNumber(a.score) ?? -Infinity)
                );
                break;

            case "price-desc":
                sortedResults.sort(
                    (a, b) =>
                        (toFiniteNumber(b.price) ?? -Infinity) -
                        (toFiniteNumber(a.price) ?? -Infinity)
                );
                break;

            case "price-asc":
                sortedResults.sort(
                    (a, b) =>
                        (toFiniteNumber(a.price) ?? Infinity) -
                        (toFiniteNumber(b.price) ?? Infinity)
                );
                break;

            case "symbol-asc":
            default:
                sortedResults.sort((a, b) =>
                    a.symbol.localeCompare(b.symbol)
                );
        }

        return sortedResults;
    }

    function renderCurrentResults() {
        const tableBody = getStrategyTableBody();

        if (!tableBody) {
            return;
        }

        const filterValue = String(
            getFilterInput()?.value || ""
        )
            .trim()
            .toUpperCase();

        const sortValue =
            getSortSelect()?.value || "symbol-asc";

        const filteredResults = currentResults.filter((result) => {
            if (!filterValue) {
                return true;
            }

            return (
                result.symbol.includes(filterValue) ||
                result.company.toUpperCase().includes(filterValue) ||
                String(result.signal)
                    .toUpperCase()
                    .includes(filterValue) ||
                String(result.rating)
                    .toUpperCase()
                    .includes(filterValue)
            );
        });

        if (filteredResults.length === 0) {
            showTableMessage(
                currentResults.length === 0
                    ? "No strategy results are available."
                    : "No research results match your filter.",
                "strategy-empty-row"
            );
            return;
        }

        const fragment = document.createDocumentFragment();

        sortResults(filteredResults, sortValue).forEach((result) => {
            fragment.appendChild(createSuccessRow(result));
        });

        tableBody.replaceChildren(fragment);
    }

    // =====================================================
    // Research loading
    // =====================================================

    async function analyzeSymbol(symbol, signal) {
        return fetchStrategy(symbol, signal);
    }

    async function loadStrategyLab(options = {}) {
        const tableBody = getStrategyTableBody();

        if (!tableBody) {
            console.error(
                'Strategy table was not found. Expected id="strategy-table".'
            );
            return;
        }

        const symbols = Array.isArray(options.symbols)
            ? [
                ...new Set(
                    options.symbols
                        .map(normalizeSymbol)
                        .filter(Boolean)
                )
            ]
            : getWatchlistSymbols();

        activeController?.abort();

        const controller = new AbortController();
        activeController = controller;

        const requestId = ++activeRequestId;
        currentResults = [];

        if (symbols.length === 0) {
            showTableMessage(
                "Your watchlist is empty. Add a stock before running AI research.",
                "strategy-empty-row"
            );
            return;
        }

        const loadingRows = new Map();
        const fragment = document.createDocumentFragment();

        symbols.forEach((symbol) => {
            const row = createLoadingRow(symbol);
            loadingRows.set(symbol, row);
            fragment.appendChild(row);
        });

        tableBody.replaceChildren(fragment);

        const tasks = symbols.map(async (symbol) => {
            try {
                const result = await analyzeSymbol(
                    symbol,
                    controller.signal
                );

                if (
                    controller.signal.aborted ||
                    requestId !== activeRequestId
                ) {
                    return;
                }

                currentResults.push(result);

                const loadingRow = loadingRows.get(symbol);

                if (loadingRow?.isConnected) {
                    loadingRow.replaceWith(
                        createSuccessRow(result)
                    );
                }
            } catch (error) {
                if (
                    error?.name === "AbortError" ||
                    controller.signal.aborted ||
                    requestId !== activeRequestId
                ) {
                    return;
                }

                console.error(
                    `Could not analyze ${symbol}:`,
                    error
                );

                const loadingRow = loadingRows.get(symbol);

                if (loadingRow?.isConnected) {
                    loadingRow.replaceWith(
                        createErrorRow(symbol, error)
                    );
                }
            }
        });

        await Promise.allSettled(tasks);

        if (
            controller.signal.aborted ||
            requestId !== activeRequestId
        ) {
            return;
        }

        activeController = null;

        if (currentResults.length > 0) {
            renderCurrentResults();
        }
    }

    function cancelStrategyLab() {
        activeController?.abort();
        activeController = null;
    }

    function refreshStrategyLab() {
        return loadStrategyLab();
    }

    // =====================================================
    // Navigation compatibility
    // =====================================================

    function showSection(sectionId) {
        if (
            typeof window.showSection === "function" &&
            window.showSection !== showSection
        ) {
            window.showSection(sectionId);
            return;
        }

        const selectedSection = document.getElementById(sectionId);

        if (!selectedSection) {
            console.error(`Section "${sectionId}" was not found.`);
            return;
        }

        document
            .querySelectorAll(".app-section, .strategy-section")
            .forEach((section) => {
                section.classList.toggle(
                    "hidden",
                    section !== selectedSection
                );
            });

        document.querySelectorAll(".nav-button").forEach((button) => {
            const isActive = button.dataset.section === sectionId;

            button.classList.toggle("active", isActive);
            button.setAttribute(
                "aria-current",
                isActive ? "page" : "false"
            );
        });

        if (
            sectionId === "strategy-section" ||
            sectionId === "research-section"
        ) {
            loadStrategyLab();
        }
    }

    // =====================================================
    // Event binding
    // =====================================================

    function bindNavigation() {
        document.querySelectorAll(".nav-button[data-section]").forEach(
            (button) => {
                if (button.dataset.strategyNavigationBound === "true") {
                    return;
                }

                button.dataset.strategyNavigationBound = "true";

                button.addEventListener("click", () => {
                    const sectionId = button.dataset.section;

                    if (sectionId) {
                        showSection(sectionId);
                    }
                });
            }
        );
    }

    function bindControls() {
        const filterInput = getFilterInput();
        const sortSelect = getSortSelect();

        filterInput?.addEventListener("input", renderCurrentResults);
        sortSelect?.addEventListener("change", renderCurrentResults);

        const refreshButton =
            document.getElementById("refresh-strategy") ||
            document.getElementById("refresh-research") ||
            document.getElementById("strategy-refresh-button");

        refreshButton?.addEventListener("click", refreshStrategyLab);
    }

    function initializeStrategyLab() {
        bindNavigation();
        bindControls();

        window.addEventListener("watchlist:updated", () => {
            const strategySection =
                document.getElementById("strategy-section") ||
                document.getElementById("research-section");

            if (
                strategySection &&
                !strategySection.classList.contains("hidden")
            ) {
                loadStrategyLab();
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener(
            "DOMContentLoaded",
            initializeStrategyLab,
            { once: true }
        );
    } else {
        initializeStrategyLab();
    }

    // =====================================================
    // Public API
    // =====================================================

    if (typeof window.showSection !== "function") {
        window.showSection = showSection;
    }

    window.loadStrategyLab = loadStrategyLab;
    window.refreshStrategyLab = refreshStrategyLab;
    window.cancelStrategyLab = cancelStrategyLab;
})();