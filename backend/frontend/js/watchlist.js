"use strict";

// ==========================================
// Watchlist
// ==========================================

const DEFAULT_WATCHLIST = Object.freeze([
    "SOFI",
    "PLTR",
    "RKLB",
    "ASTS",
    "IONQ",
    "QBTS",
    "RGTI",
    "HIMS",
    "ACHR",
    "LCID"
]);

const WATCHLIST_STORAGE_KEY = "watchlist";
const WATCHLIST_REQUEST_TIMEOUT_MS = 15_000;
const WATCHLIST_MAX_SYMBOLS = 100;

let watchlist =
    loadStoredWatchlist();

let activeWatchlistRequest = null;
let activeWatchSelectionRequest = null;

let latestWatchlistRequestNumber = 0;
let latestWatchSelectionRequestNumber = 0;

const watchlistCurrencyFormatter =
    new Intl.NumberFormat(
        "en-US",
        {
            style: "currency",
            currency: "USD",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }
    );


// ==========================================
// Startup
// ==========================================

document.addEventListener(
    "DOMContentLoaded",
    initializeWatchlist
);


function initializeWatchlist() {
    synchronizeGlobalWatchlist();

    const container =
        document.getElementById(
            "watchlist"
        );

    if (!container) {
        return;
    }

    container.addEventListener(
        "click",
        handleWatchlistClick
    );

    container.addEventListener(
        "keydown",
        handleWatchlistKeydown
    );

    loadWatchlist();
}


// ==========================================
// Storage
// ==========================================

function loadStoredWatchlist() {
    try {
        const rawValue =
            window.localStorage.getItem(
                WATCHLIST_STORAGE_KEY
            );

        if (!rawValue) {
            return [...DEFAULT_WATCHLIST];
        }

        const parsedValue =
            JSON.parse(rawValue);

        if (!Array.isArray(parsedValue)) {
            throw new Error(
                "Stored watchlist is not an array."
            );
        }

        const normalized =
            normalizeWatchlist(
                parsedValue
            );

        return normalized.length > 0
            ? normalized
            : [];
    } catch (error) {
        console.warn(
            "Could not read the saved watchlist:",
            error
        );

        return [...DEFAULT_WATCHLIST];
    }
}


function saveWatchlist() {
    watchlist =
        normalizeWatchlist(
            watchlist
        );

    synchronizeGlobalWatchlist();

    try {
        window.localStorage.setItem(
            WATCHLIST_STORAGE_KEY,
            JSON.stringify(
                watchlist
            )
        );

        return true;
    } catch (error) {
        console.error(
            "Could not save the watchlist:",
            error
        );

        showWatchlistMessage(
            "The watchlist could not be saved in this browser.",
            "error"
        );

        return false;
    }
}


function synchronizeGlobalWatchlist() {
    window.watchlist =
        [...watchlist];
}


// ==========================================
// Loading and rendering
// ==========================================

async function loadWatchlist() {
    const container =
        document.getElementById(
            "watchlist"
        );

    if (!container) {
        console.error(
            'Watchlist container was not found. Expected id="watchlist".'
        );

        return;
    }

    cancelActiveWatchlistRequest();

    const symbols =
        normalizeWatchlist(
            watchlist
        );

    watchlist = symbols;
    synchronizeGlobalWatchlist();

    if (symbols.length === 0) {
        renderEmptyWatchlist(
            container
        );

        return;
    }

    const controller =
        new AbortController();

    activeWatchlistRequest =
        controller;

    const requestNumber =
        ++latestWatchlistRequestNumber;

    let requestTimedOut = false;

    const timeoutId =
        window.setTimeout(
            () => {
                requestTimedOut = true;
                controller.abort();
            },
            WATCHLIST_REQUEST_TIMEOUT_MS
        );

    renderWatchlistLoading(
        container,
        symbols.length
    );

    try {
        const quoteResults =
            await Promise.all(
                symbols.map(
                    symbol =>
                        fetchWatchlistQuote(
                            symbol,
                            controller.signal
                        )
                )
            );

        if (
            requestNumber !==
            latestWatchlistRequestNumber
        ) {
            return;
        }

        renderWatchlistItems(
            container,
            quoteResults
        );
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            if (
                requestNumber !==
                latestWatchlistRequestNumber
            ) {
                return;
            }

            if (requestTimedOut) {
                renderWatchlistError(
                    container,
                    "The watchlist took too long to load."
                );
            }

            return;
        }

        console.error(
            "Watchlist loading failed:",
            error
        );

        if (
            requestNumber ===
            latestWatchlistRequestNumber
        ) {
            renderWatchlistError(
                container,
                "Unable to load the watchlist."
            );
        }
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeWatchlistRequest ===
            controller
        ) {
            activeWatchlistRequest =
                null;
        }
    }
}


async function fetchWatchlistQuote(
    symbol,
    signal
) {
    try {
        const response = await fetch(
            `${getWatchlistApiUrl()}/quote/${encodeURIComponent(
                symbol
            )}`,
            {
                method: "GET",

                headers: {
                    Accept:
                        "application/json"
                },

                signal
            }
        );

        const payload =
            await readWatchlistJson(
                response
            );

        if (!response.ok) {
            throw new Error(
                getWatchlistResponseMessage(
                    payload,
                    `Quote request failed with status ${response.status}.`
                )
            );
        }

        const price =
            normalizeWatchlistQuotePrice(
                payload
            );

        const changePercent =
            normalizeNullableWatchlistNumber(
                payload?.change_percent ??
                payload?.percent_change ??
                payload?.changePercent
            );

        return {
            symbol,
            price,
            changePercent,
            available:
                price !== null
        };
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            throw error;
        }

        console.warn(
            `Could not load a quote for ${symbol}:`,
            error
        );

        return {
            symbol,
            price: null,
            changePercent: null,
            available: false
        };
    }
}


function renderWatchlistItems(
    container,
    quoteResults
) {
    container.replaceChildren();

    const fragment =
        document.createDocumentFragment();

    for (
        const quote of
        quoteResults
    ) {
        fragment.appendChild(
            createWatchItem(
                quote
            )
        );
    }

    container.appendChild(
        fragment
    );

    container.removeAttribute(
        "aria-busy"
    );
}


function createWatchItem(
    quote
) {
    const wrapper =
        document.createElement(
            "div"
        );

    wrapper.className =
        "watch-item";

    wrapper.dataset.symbol =
        quote.symbol;

    const selectButton =
        document.createElement(
            "button"
        );

    selectButton.type =
        "button";

    selectButton.className =
        "watch-stock";

    selectButton.dataset.action =
        "select";

    selectButton.dataset.symbol =
        quote.symbol;

    selectButton.setAttribute(
        "aria-label",
        `Select ${quote.symbol}`
    );

    const identity =
        document.createElement(
            "span"
        );

    identity.className =
        "watch-stock-identity";

    const symbolElement =
        document.createElement(
            "strong"
        );

    symbolElement.textContent =
        quote.symbol;

    const priceElement =
        document.createElement(
            "span"
        );

    priceElement.className =
        quote.available
            ? "watch-price"
            : "watch-price watch-price-unavailable";

    priceElement.textContent =
        quote.available
            ? formatWatchlistCurrency(
                quote.price
            )
            : "Unavailable";

    identity.append(
        symbolElement,
        priceElement
    );

    selectButton.appendChild(
        identity
    );

    if (
        quote.changePercent !==
        null
    ) {
        const changeElement =
            document.createElement(
                "span"
            );

        changeElement.className =
            quote.changePercent >= 0
                ? "watch-change positive"
                : "watch-change negative";

        changeElement.textContent =
            formatWatchlistPercent(
                quote.changePercent
            );

        selectButton.appendChild(
            changeElement
        );
    }

    const removeButton =
        document.createElement(
            "button"
        );

    removeButton.type =
        "button";

    removeButton.className =
        "remove-watch-button";

    removeButton.dataset.action =
        "remove";

    removeButton.dataset.symbol =
        quote.symbol;

    removeButton.title =
        `Remove ${quote.symbol}`;

    removeButton.setAttribute(
        "aria-label",
        `Remove ${quote.symbol} from watchlist`
    );

    removeButton.textContent =
        "×";

    wrapper.append(
        selectButton,
        removeButton
    );

    return wrapper;
}


function renderWatchlistLoading(
    container,
    symbolCount
) {
    container.replaceChildren();

    container.setAttribute(
        "aria-busy",
        "true"
    );

    const status =
        document.createElement(
            "p"
        );

    status.className =
        "watchlist-status watchlist-loading";

    status.setAttribute(
        "role",
        "status"
    );

    status.textContent =
        `Loading ${symbolCount} watchlist stock${symbolCount === 1 ? "" : "s"}…`;

    container.appendChild(
        status
    );
}


function renderEmptyWatchlist(
    container
) {
    container.replaceChildren();

    container.removeAttribute(
        "aria-busy"
    );

    const status =
        document.createElement(
            "p"
        );

    status.className =
        "watchlist-status watchlist-empty";

    status.textContent =
        "Your watchlist is empty. Select a stock and add it here.";

    container.appendChild(
        status
    );
}


function renderWatchlistError(
    container,
    message
) {
    container.replaceChildren();

    container.removeAttribute(
        "aria-busy"
    );

    const status =
        document.createElement(
            "p"
        );

    status.className =
        "watchlist-status watchlist-error";

    status.setAttribute(
        "role",
        "alert"
    );

    status.textContent =
        message;

    const retryButton =
        document.createElement(
            "button"
        );

    retryButton.type =
        "button";

    retryButton.className =
        "watchlist-retry-button";

    retryButton.textContent =
        "Try Again";

    retryButton.addEventListener(
        "click",
        loadWatchlist
    );

    container.append(
        status,
        retryButton
    );
}


// ==========================================
// Stock selection
// ==========================================

async function selectWatchStock(
    requestedSymbol
) {
    const symbol =
        normalizeWatchlistSymbol(
            requestedSymbol
        );

    if (!symbol) {
        return;
    }

    cancelActiveWatchSelectionRequest();

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
            symbol;
    }

    if (priceInput) {
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

    if (
        typeof window.loadChart ===
        "function"
    ) {
        try {
            window.loadChart(
                symbol
            );
        } catch (error) {
            console.warn(
                "Could not load the selected stock chart:",
                error
            );
        }
    }

    const controller =
        new AbortController();

    activeWatchSelectionRequest =
        controller;

    const requestNumber =
        ++latestWatchSelectionRequestNumber;

    let requestTimedOut = false;

    const timeoutId =
        window.setTimeout(
            () => {
                requestTimedOut = true;
                controller.abort();
            },
            WATCHLIST_REQUEST_TIMEOUT_MS
        );

    try {
        const response = await fetch(
            `${getWatchlistApiUrl()}/quote/${encodeURIComponent(
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

        const payload =
            await readWatchlistJson(
                response
            );

        if (!response.ok) {
            throw new Error(
                getWatchlistResponseMessage(
                    payload,
                    `Quote request failed with status ${response.status}.`
                )
            );
        }

        if (
            requestNumber !==
            latestWatchSelectionRequestNumber
        ) {
            return;
        }

        const price =
            normalizeWatchlistQuotePrice(
                payload
            );

        if (price === null) {
            throw new Error(
                "No valid price was returned."
            );
        }

        if (priceInput) {
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

            priceInput.dispatchEvent(
                new Event(
                    "change",
                    {
                        bubbles: true
                    }
                )
            );
        }

        clearWatchlistTradeError();

        const selectionDetail = {
            symbol,
            price
        };

        document.dispatchEvent(
            new CustomEvent(
                "watchlist:stock-selected",
                {
                    detail:
                        selectionDetail
                }
            )
        );

        /*
         * trades.js already listens for this event name.
         * Dispatching it keeps the Trade Center synchronized
         * without directly depending on its internal functions.
         */
        document.dispatchEvent(
            new CustomEvent(
                "search:stock-selected",
                {
                    detail:
                        selectionDetail
                }
            )
        );
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            if (
                requestNumber !==
                latestWatchSelectionRequestNumber
            ) {
                return;
            }

            if (!requestTimedOut) {
                return;
            }

            setWatchlistSelectionError(
                symbol,
                "The price request timed out."
            );

            return;
        }

        console.error(
            `Could not select ${symbol}:`,
            error
        );

        if (
            requestNumber ===
            latestWatchSelectionRequestNumber
        ) {
            setWatchlistSelectionError(
                symbol,
                error?.message ||
                "The latest price is unavailable."
            );
        }
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeWatchSelectionRequest ===
            controller
        ) {
            activeWatchSelectionRequest =
                null;
        }
    }
}


// ==========================================
// Adding and removing symbols
// ==========================================

function addCurrentStock() {
    const symbolInput =
        document.getElementById(
            "symbol"
        );

    const symbol =
        normalizeWatchlistSymbol(
            symbolInput?.value
        );

    if (!symbol) {
        showWatchlistMessage(
            "Search for or enter a valid stock symbol first.",
            "error"
        );

        symbolInput?.focus();

        return false;
    }

    if (
        watchlist.includes(
            symbol
        )
    ) {
        showWatchlistMessage(
            `${symbol} is already in your watchlist.`,
            "error"
        );

        return false;
    }

    if (
        watchlist.length >=
        WATCHLIST_MAX_SYMBOLS
    ) {
        showWatchlistMessage(
            `Your watchlist can contain up to ${WATCHLIST_MAX_SYMBOLS} symbols.`,
            "error"
        );

        return false;
    }

    watchlist.push(
        symbol
    );

    watchlist =
        normalizeWatchlist(
            watchlist
        );

    saveWatchlist();
    loadWatchlist();

    showWatchlistMessage(
        `${symbol} was added to your watchlist.`,
        "success"
    );

    document.dispatchEvent(
        new CustomEvent(
            "watchlist:changed",
            {
                detail: {
                    action: "add",
                    symbol,
                    watchlist:
                        [...watchlist]
                }
            }
        )
    );

    return true;
}


function removeFromWatchlist(
    requestedSymbol
) {
    const symbol =
        normalizeWatchlistSymbol(
            requestedSymbol
        );

    if (!symbol) {
        return false;
    }

    const previousLength =
        watchlist.length;

    watchlist =
        watchlist.filter(
            item =>
                item !== symbol
        );

    if (
        watchlist.length ===
        previousLength
    ) {
        return false;
    }

    saveWatchlist();
    loadWatchlist();

    showWatchlistMessage(
        `${symbol} was removed from your watchlist.`,
        "success"
    );

    document.dispatchEvent(
        new CustomEvent(
            "watchlist:changed",
            {
                detail: {
                    action: "remove",
                    symbol,
                    watchlist:
                        [...watchlist]
                }
            }
        )
    );

    return true;
}


function resetWatchlist() {
    watchlist =
        [...DEFAULT_WATCHLIST];

    saveWatchlist();
    loadWatchlist();

    showWatchlistMessage(
        "The default watchlist was restored.",
        "success"
    );

    document.dispatchEvent(
        new CustomEvent(
            "watchlist:changed",
            {
                detail: {
                    action: "reset",
                    watchlist:
                        [...watchlist]
                }
            }
        )
    );
}


// ==========================================
// Interaction
// ==========================================

function handleWatchlistClick(
    event
) {
    const button =
        event.target.closest(
            "button[data-action]"
        );

    if (!button) {
        return;
    }

    const action =
        button.dataset.action;

    const symbol =
        normalizeWatchlistSymbol(
            button.dataset.symbol
        );

    if (!symbol) {
        return;
    }

    if (
        action === "select"
    ) {
        selectWatchStock(
            symbol
        );

        return;
    }

    if (
        action === "remove"
    ) {
        removeFromWatchlist(
            symbol
        );
    }
}


function handleWatchlistKeydown(
    event
) {
    if (
        event.key !== "Enter" &&
        event.key !== " "
    ) {
        return;
    }

    const item =
        event.target.closest(
            ".watch-item"
        );

    if (
        !item ||
        event.target.closest(
            "button"
        )
    ) {
        return;
    }

    const symbol =
        normalizeWatchlistSymbol(
            item.dataset.symbol
        );

    if (!symbol) {
        return;
    }

    event.preventDefault();

    selectWatchStock(
        symbol
    );
}


// ==========================================
// Messages and field state
// ==========================================

function showWatchlistMessage(
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
        message.classList.add(
            type
        );
    }

    message.setAttribute(
        "role",
        type === "error"
            ? "alert"
            : "status"
    );
}


function clearWatchlistTradeError() {
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


function setWatchlistSelectionError(
    symbol,
    reason
) {
    const priceInput =
        document.getElementById(
            "price"
        );

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

    showWatchlistMessage(
        `Could not load the latest price for ${symbol}. ${reason}`,
        "error"
    );
}


// ==========================================
// Request helpers
// ==========================================

function cancelActiveWatchlistRequest() {
    if (
        activeWatchlistRequest
    ) {
        activeWatchlistRequest.abort();

        activeWatchlistRequest =
            null;
    }
}


function cancelActiveWatchSelectionRequest() {
    if (
        activeWatchSelectionRequest
    ) {
        activeWatchSelectionRequest.abort();

        activeWatchSelectionRequest =
            null;
    }
}


async function readWatchlistJson(
    response
) {
    try {
        return await response.json();
    } catch {
        return null;
    }
}


function getWatchlistResponseMessage(
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


function getWatchlistApiUrl() {
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
// Normalization
// ==========================================

function normalizeWatchlist(
    symbols
) {
    if (!Array.isArray(symbols)) {
        return [];
    }

    return Array.from(
        new Set(
            symbols
                .map(
                    normalizeWatchlistSymbol
                )
                .filter(Boolean)
        )
    ).slice(
        0,
        WATCHLIST_MAX_SYMBOLS
    );
}


function normalizeWatchlistSymbol(
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


function normalizeWatchlistQuotePrice(
    payload
) {
    if (
        !payload ||
        typeof payload !== "object" ||
        Array.isArray(payload)
    ) {
        return null;
    }

    return firstPositiveWatchlistNumber(
        payload.price,
        payload.current_price,
        payload.last_price,
        payload.regularMarketPrice,
        payload.quote?.price
    );
}


function firstPositiveWatchlistNumber(
    ...values
) {
    for (
        const value of
        values
    ) {
        const number =
            Number(value);

        if (
            Number.isFinite(number) &&
            number > 0
        ) {
            return number;
        }
    }

    return null;
}


function normalizeNullableWatchlistNumber(
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


// ==========================================
// Formatting
// ==========================================

function formatWatchlistCurrency(
    value
) {
    const number =
        Number(value);

    if (!Number.isFinite(number)) {
        return "Unavailable";
    }

    return watchlistCurrencyFormatter.format(
        number
    );
}


function formatWatchlistPercent(
    value
) {
    const number =
        Number(value);

    if (!Number.isFinite(number)) {
        return "";
    }

    const sign =
        number > 0
            ? "+"
            : "";

    return `${sign}${number.toFixed(2)}%`;
}


// ==========================================
// Public API
// ==========================================

window.loadWatchlist =
    loadWatchlist;

window.addCurrentStock =
    addCurrentStock;

window.removeFromWatchlist =
    removeFromWatchlist;

window.selectWatchStock =
    selectWatchStock;

window.resetWatchlist =
    resetWatchlist;

window.saveWatchlist =
    saveWatchlist;