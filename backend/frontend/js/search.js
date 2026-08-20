"use strict";

let symbolInput = null;
let priceInput = null;
let searchResults = null;
let searchWrapper = null;

let searchTimer = null;
let activeSearchRequest = null;
let activeQuoteRequest = null;
let latestSearchRequestNumber = 0;
let latestQuoteRequestNumber = 0;
let highlightedSearchIndex = -1;

const SEARCH_DEBOUNCE_MS = 350;
const SEARCH_REQUEST_TIMEOUT_MS = 12_000;
const QUOTE_REQUEST_TIMEOUT_MS = 12_000;
const MAX_SEARCH_RESULTS = 10;


function initializeStockSearch() {
    symbolInput =
        document.getElementById(
            "symbol"
        );

    priceInput =
        document.getElementById(
            "price"
        );

    searchResults =
        document.getElementById(
            "searchResults"
        );

    searchWrapper =
        document.querySelector(
            ".search-wrapper"
        );

    if (
        !symbolInput ||
        !searchResults
    ) {
        return false;
    }

    symbolInput.setAttribute(
        "autocomplete",
        "off"
    );

    symbolInput.setAttribute(
        "aria-autocomplete",
        "list"
    );

    symbolInput.setAttribute(
        "aria-expanded",
        "false"
    );

    symbolInput.setAttribute(
        "aria-controls",
        "searchResults"
    );

    searchResults.setAttribute(
        "role",
        "listbox"
    );

    symbolInput.addEventListener(
        "input",
        handleSymbolInput
    );

    symbolInput.addEventListener(
        "keydown",
        handleSearchKeydown
    );

    symbolInput.addEventListener(
        "focus",
        handleSearchFocus
    );

    searchResults.addEventListener(
        "click",
        handleSearchResultClick
    );

    document.addEventListener(
        "click",
        handleOutsideSearchClick
    );

    return true;
}


function handleSymbolInput() {
    window.clearTimeout(
        searchTimer
    );

    const query =
        normalizeSearchQuery(
            symbolInput?.value
        );

    highlightedSearchIndex = -1;

    if (!query) {
        cancelActiveSearchRequest();
        hideSearchResults();

        return;
    }

    searchTimer =
        window.setTimeout(
            () => {
                searchStocks(query);
            },
            SEARCH_DEBOUNCE_MS
        );
}


function handleSearchFocus() {
    const hasResults =
        searchResults?.children
            .length > 0;

    if (hasResults) {
        showSearchResultsContainer();
    }
}


async function searchStocks(query) {
    const cleanQuery =
        normalizeSearchQuery(
            query
        );

    if (!cleanQuery) {
        hideSearchResults();
        return;
    }

    cancelActiveSearchRequest();

    const requestController =
        new AbortController();

    activeSearchRequest =
        requestController;

    const requestNumber =
        ++latestSearchRequestNumber;

    let requestTimedOut = false;

    const timeoutId =
        window.setTimeout(
            () => {
                requestTimedOut = true;
                requestController.abort();
            },
            SEARCH_REQUEST_TIMEOUT_MS
        );

    showSearchLoadingState();

    try {
        const response = await fetch(
            `${getSearchApiUrl()}/search?query=${encodeURIComponent(
                cleanQuery
            )}`,
            {
                method: "GET",

                headers: {
                    Accept:
                        "application/json",
                },

                signal:
                    requestController.signal,
            }
        );

        const payload =
            await readSearchJson(
                response
            );

        if (!response.ok) {
            const message =
                payload?.detail ??
                payload?.error ??
                `Stock search failed with status ${response.status}.`;

            throw new Error(
                String(message)
            );
        }

        if (
            requestNumber !==
            latestSearchRequestNumber
        ) {
            return;
        }

        const stocks =
            normalizeSearchResults(
                payload
            );

        renderSearchResults(
            stocks
        );
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            if (
                requestNumber !==
                latestSearchRequestNumber
            ) {
                return;
            }

            if (requestTimedOut) {
                showSearchMessage(
                    "Search timed out. Try again."
                );
            }

            return;
        }

        console.error(
            "Search error:",
            error
        );

        if (
            requestNumber ===
            latestSearchRequestNumber
        ) {
            showSearchMessage(
                "Unable to search stocks."
            );
        }
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeSearchRequest ===
            requestController
        ) {
            activeSearchRequest =
                null;
        }
    }
}


function normalizeSearchResults(
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
            payload.results ??
            payload.stocks ??
            payload.data ??
            [];
    }

    if (!Array.isArray(stocks)) {
        throw new Error(
            "The stock search response must contain an array."
        );
    }

    const uniqueStocks =
        new Map();

    for (const stock of stocks) {
        if (
            !stock ||
            typeof stock !==
                "object"
        ) {
            continue;
        }

        const symbol =
            normalizeSearchSymbol(
                stock.symbol ??
                stock.ticker
            );

        if (!symbol) {
            continue;
        }

        const normalizedStock = {
            symbol,

            name:
                normalizeSearchText(
                    stock.name ??
                    stock.company ??
                    stock.company_name
                ),

            exchange:
                normalizeSearchText(
                    stock.exchange ??
                    stock.market
                ),
        };

        if (
            !uniqueStocks.has(
                symbol
            )
        ) {
            uniqueStocks.set(
                symbol,
                normalizedStock
            );
        }
    }

    return Array.from(
        uniqueStocks.values()
    ).slice(
        0,
        MAX_SEARCH_RESULTS
    );
}


function renderSearchResults(
    stocks
) {
    if (!searchResults) {
        return;
    }

    searchResults.replaceChildren();
    highlightedSearchIndex = -1;

    if (stocks.length === 0) {
        showSearchMessage(
            "No matching stocks found."
        );

        return;
    }

    const fragment =
        document.createDocumentFragment();

    stocks.forEach(
        (stock, index) => {
            fragment.appendChild(
                createSearchResultButton(
                    stock,
                    index
                )
            );
        }
    );

    searchResults.appendChild(
        fragment
    );

    showSearchResultsContainer();
}


function createSearchResultButton(
    stock,
    index
) {
    const button =
        document.createElement(
            "button"
        );

    button.type = "button";
    button.className =
        "search-result";

    button.dataset.symbol =
        stock.symbol;

    button.dataset.index =
        String(index);

    button.id =
        `search-result-${index}`;

    button.setAttribute(
        "role",
        "option"
    );

    button.setAttribute(
        "aria-selected",
        "false"
    );

    const identity =
        document.createElement(
            "span"
        );

    identity.className =
        "search-result-identity";

    const symbol =
        document.createElement(
            "strong"
        );

    symbol.textContent =
        stock.symbol;

    identity.appendChild(
        symbol
    );

    if (stock.name) {
        const name =
            document.createElement(
                "span"
            );

        name.textContent =
            stock.name;

        identity.appendChild(
            name
        );
    }

    button.appendChild(
        identity
    );

    if (stock.exchange) {
        const exchange =
            document.createElement(
                "small"
            );

        exchange.className =
            "search-result-exchange";

        exchange.textContent =
            stock.exchange;

        button.appendChild(
            exchange
        );
    }

    return button;
}


async function selectSearchStock(
    symbol
) {
    const cleanSymbol =
        normalizeSearchSymbol(
            symbol
        );

    if (!cleanSymbol) {
        return;
    }

    cancelActiveSearchRequest();
    cancelActiveQuoteRequest();

    if (symbolInput) {
        symbolInput.value =
            cleanSymbol;

        symbolInput.setAttribute(
            "aria-expanded",
            "false"
        );
    }

    hideSearchResults();

    setQuoteLoadingState();

    const requestController =
        new AbortController();

    activeQuoteRequest =
        requestController;

    const requestNumber =
        ++latestQuoteRequestNumber;

    let requestTimedOut = false;

    const timeoutId =
        window.setTimeout(
            () => {
                requestTimedOut = true;
                requestController.abort();
            },
            QUOTE_REQUEST_TIMEOUT_MS
        );

    try {
        const response = await fetch(
            `${getSearchApiUrl()}/quote/${encodeURIComponent(
                cleanSymbol
            )}`,
            {
                method: "GET",

                headers: {
                    Accept:
                        "application/json",
                },

                signal:
                    requestController.signal,
            }
        );

        const payload =
            await readSearchJson(
                response
            );

        if (!response.ok) {
            const message =
                payload?.detail ??
                payload?.error ??
                `Price request failed with status ${response.status}.`;

            throw new Error(
                String(message)
            );
        }

        if (
            requestNumber !==
            latestQuoteRequestNumber
        ) {
            return;
        }

        const price =
            normalizeQuotePrice(
                payload
            );

        if (price === null) {
            throw new Error(
                "The quote response did not contain a valid price."
            );
        }

        if (priceInput) {
            priceInput.value =
                price.toFixed(2);

            priceInput.placeholder =
                "0.00";

            priceInput.dispatchEvent(
                new Event(
                    "change",
                    {
                        bubbles: true,
                    }
                )
            );
        }

        clearTradeSearchMessage();

        if (
            typeof window.loadChart ===
            "function"
        ) {
            window.loadChart(
                cleanSymbol
            );
        }

        document.dispatchEvent(
            new CustomEvent(
                "search:stock-selected",
                {
                    detail: {
                        symbol:
                            cleanSymbol,

                        price,
                    },
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
                latestQuoteRequestNumber
            ) {
                return;
            }

            const message =
                requestTimedOut
                    ? `The price request for ${cleanSymbol} timed out.`
                    : `The price request for ${cleanSymbol} was cancelled.`;

            setQuoteErrorState(
                message
            );

            return;
        }

        console.error(
            "Quote error:",
            error
        );

        if (
            requestNumber ===
            latestQuoteRequestNumber
        ) {
            setQuoteErrorState(
                `Could not load the latest price for ${cleanSymbol}.`
            );
        }
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeQuoteRequest ===
            requestController
        ) {
            activeQuoteRequest =
                null;
        }
    }
}


function normalizeQuotePrice(
    payload
) {
    if (
        !payload ||
        typeof payload !==
            "object" ||
        Array.isArray(payload)
    ) {
        return null;
    }

    const price =
        Number(
            payload.price ??
            payload.current_price ??
            payload.last_price ??
            payload.regularMarketPrice
        );

    if (
        !Number.isFinite(price) ||
        price <= 0
    ) {
        return null;
    }

    return price;
}


function handleSearchResultClick(
    event
) {
    const button =
        event.target.closest(
            ".search-result[data-symbol]"
        );

    if (!button) {
        return;
    }

    selectSearchStock(
        button.dataset.symbol
    );
}


function handleSearchKeydown(
    event
) {
    const buttons =
        getSearchResultButtons();

    if (
        event.key ===
        "Escape"
    ) {
        hideSearchResults();
        return;
    }

    if (
        buttons.length === 0
    ) {
        if (
            event.key ===
            "Enter"
        ) {
            const symbol =
                normalizeSearchSymbol(
                    symbolInput?.value
                );

            if (symbol) {
                event.preventDefault();

                selectSearchStock(
                    symbol
                );
            }
        }

        return;
    }

    if (
        event.key ===
        "ArrowDown"
    ) {
        event.preventDefault();

        highlightedSearchIndex =
            highlightedSearchIndex <
            buttons.length - 1
                ? highlightedSearchIndex + 1
                : 0;

        updateHighlightedResult(
            buttons
        );

        return;
    }

    if (
        event.key ===
        "ArrowUp"
    ) {
        event.preventDefault();

        highlightedSearchIndex =
            highlightedSearchIndex > 0
                ? highlightedSearchIndex - 1
                : buttons.length - 1;

        updateHighlightedResult(
            buttons
        );

        return;
    }

    if (
        event.key ===
        "Enter"
    ) {
        event.preventDefault();

        const selectedButton =
            buttons[
                highlightedSearchIndex
            ];

        if (selectedButton) {
            selectSearchStock(
                selectedButton.dataset
                    .symbol
            );

            return;
        }

        const symbol =
            normalizeSearchSymbol(
                symbolInput?.value
            );

        if (symbol) {
            selectSearchStock(
                symbol
            );
        }
    }
}


function updateHighlightedResult(
    buttons
) {
    buttons.forEach(
        (button, index) => {
            const isSelected =
                index ===
                highlightedSearchIndex;

            button.classList.toggle(
                "search-result-active",
                isSelected
            );

            button.setAttribute(
                "aria-selected",
                String(isSelected)
            );
        }
    );

    const selectedButton =
        buttons[
            highlightedSearchIndex
        ];

    if (selectedButton) {
        symbolInput?.setAttribute(
            "aria-activedescendant",
            selectedButton.id
        );

        selectedButton.scrollIntoView({
            block: "nearest",
        });
    }
}


function getSearchResultButtons() {
    if (!searchResults) {
        return [];
    }

    return Array.from(
        searchResults.querySelectorAll(
            ".search-result[data-symbol]"
        )
    );
}


function handleOutsideSearchClick(
    event
) {
    if (
        searchWrapper &&
        !searchWrapper.contains(
            event.target
        )
    ) {
        hideSearchResults();
    }
}


function showSearchLoadingState() {
    showSearchMessage(
        "Searching stocks…",
        true
    );
}


function showSearchMessage(
    message,
    isLoading = false
) {
    if (!searchResults) {
        return;
    }

    searchResults.replaceChildren();

    const status =
        document.createElement(
            "div"
        );

    status.className =
        "search-status-message";

    if (isLoading) {
        status.classList.add(
            "search-loading-message"
        );
    }

    status.setAttribute(
        "role",
        "status"
    );

    status.textContent =
        message;

    searchResults.appendChild(
        status
    );

    showSearchResultsContainer();
}


function showSearchResultsContainer() {
    if (!searchResults) {
        return;
    }

    searchResults.style.display =
        "block";

    symbolInput?.setAttribute(
        "aria-expanded",
        "true"
    );
}


function hideSearchResults() {
    if (!searchResults) {
        return;
    }

    searchResults.style.display =
        "none";

    searchResults.replaceChildren();

    highlightedSearchIndex = -1;

    symbolInput?.setAttribute(
        "aria-expanded",
        "false"
    );

    symbolInput?.removeAttribute(
        "aria-activedescendant"
    );
}


function setQuoteLoadingState() {
    if (!priceInput) {
        return;
    }

    priceInput.value = "";
    priceInput.placeholder =
        "Loading price…";

    priceInput.setAttribute(
        "aria-busy",
        "true"
    );
}


function setQuoteErrorState(
    message
) {
    if (priceInput) {
        priceInput.value = "";
        priceInput.placeholder =
            "Price unavailable";

        priceInput.removeAttribute(
            "aria-busy"
        );
    }

    const tradeMessage =
        document.getElementById(
            "trade-message"
        );

    if (tradeMessage) {
        tradeMessage.textContent =
            message;

        tradeMessage.classList.add(
            "error"
        );

        tradeMessage.setAttribute(
            "role",
            "alert"
        );
    }
}


function clearTradeSearchMessage() {
    if (priceInput) {
        priceInput.removeAttribute(
            "aria-busy"
        );
    }

    const tradeMessage =
        document.getElementById(
            "trade-message"
        );

    if (!tradeMessage) {
        return;
    }

    tradeMessage.textContent = "";
    tradeMessage.classList.remove(
        "error"
    );

    tradeMessage.removeAttribute(
        "role"
    );
}


function cancelActiveSearchRequest() {
    if (activeSearchRequest) {
        activeSearchRequest.abort();

        activeSearchRequest =
            null;
    }
}


function cancelActiveQuoteRequest() {
    if (activeQuoteRequest) {
        activeQuoteRequest.abort();

        activeQuoteRequest =
            null;
    }
}


async function readSearchJson(
    response
) {
    try {
        return await response.json();
    } catch {
        return null;
    }
}


function getSearchApiUrl() {
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


function normalizeSearchQuery(
    value
) {
    return String(
        value ?? ""
    )
        .trim()
        .slice(0, 100);
}


function normalizeSearchSymbol(
    value
) {
    return String(
        value ?? ""
    )
        .trim()
        .toUpperCase()
        .replace(
            /[^A-Z0-9.\-^]/g,
            ""
        )
        .slice(0, 20);
}


function normalizeSearchText(
    value
) {
    return String(
        value ?? ""
    )
        .trim()
        .replace(/\s+/g, " ")
        .slice(0, 200);
}


window.searchStocks =
    searchStocks;

window.selectSearchStock =
    selectSearchStock;

window.hideSearchResults =
    hideSearchResults;


document.addEventListener(
    "DOMContentLoaded",
    () => {
        initializeStockSearch();
    }
);