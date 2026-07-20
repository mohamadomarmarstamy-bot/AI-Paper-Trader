const DEFAULT_WATCHLIST = [
    "NVDA",
    "AAPL",
    "TSLA",
    "MSFT",
    "AMZN"
];

let watchlist = JSON.parse(
    localStorage.getItem("watchlist")
) || [...DEFAULT_WATCHLIST];


function saveWatchlist() {
    localStorage.setItem(
        "watchlist",
        JSON.stringify(watchlist)
    );
}


async function loadWatchlist() {
    const container = document.getElementById("watchlist");

    if (!container) {
        console.error("Watchlist container was not found.");
        return;
    }

    if (watchlist.length === 0) {
        container.innerHTML = `
            <p>Your watchlist is empty.</p>
        `;
        return;
    }

    container.innerHTML = "<p>Loading...</p>";

    const watchItems = await Promise.all(
        watchlist.map(async (symbol) => {
            try {
                const response = await fetch(
                    `http://127.0.0.1:8000/quote/${symbol}`
                );

                if (!response.ok) {
                    throw new Error(
                        `Quote request failed: ${response.status}`
                    );
                }

                const data = await response.json();
                const price = Number(data.price);

                return createWatchItem(
                    symbol,
                    Number.isFinite(price)
                        ? `$${price.toFixed(2)}`
                        : "Unavailable"
                );

            } catch (error) {
                console.error(
                    `Could not load quote for ${symbol}:`,
                    error
                );

                return createWatchItem(
                    symbol,
                    "Unavailable"
                );
            }
        })
    );

    container.innerHTML = watchItems.join("");
}


function createWatchItem(symbol, priceText) {
    return `
        <div class="watch-item" data-symbol="${symbol}">
            <button
                type="button"
                class="watch-stock"
                data-action="select"
                data-symbol="${symbol}"
            >
                <strong>${symbol}</strong>
                <span>${priceText}</span>
            </button>

            <button
                type="button"
                class="remove-watch-button"
                data-action="remove"
                data-symbol="${symbol}"
                title="Remove ${symbol}"
                aria-label="Remove ${symbol} from watchlist"
            >
                ×
            </button>
        </div>
    `;
}


async function selectWatchStock(symbol) {
    const symbolInput = document.getElementById("symbol");
    const priceInput = document.getElementById("price");

    if (!symbolInput || !priceInput) {
        console.error("Symbol or price input was not found.");
        return;
    }

    symbolInput.value = symbol;

    // Load the chart immediately instead of waiting for the quote.
    if (typeof window.loadChart === "function") {
        window.loadChart(symbol);
    } else if (typeof loadChart === "function") {
        loadChart(symbol);
    } else {
        console.error("loadChart() was not found.");
    }

    try {
        const response = await fetch(
            `http://127.0.0.1:8000/quote/${symbol}`
        );

        if (!response.ok) {
            throw new Error(
                `Quote request failed: ${response.status}`
            );
        }

        const data = await response.json();
        const price = Number(data.price);

        if (Number.isFinite(price)) {
            priceInput.value = price.toFixed(2);
        }

    } catch (error) {
        console.error(
            `Could not load ${symbol}:`,
            error
        );
    }
}


function addCurrentStock() {
    const symbolInput = document.getElementById("symbol");
    const message = document.getElementById("trade-message");

    if (!symbolInput) {
        return;
    }

    const symbol = symbolInput.value
        .trim()
        .toUpperCase();

    if (!symbol) {
        if (message) {
            message.textContent =
                "Search for or enter a stock symbol first.";
        }

        return;
    }

    if (watchlist.includes(symbol)) {
        if (message) {
            message.textContent =
                `${symbol} is already in your watchlist.`;
        }

        return;
    }

    watchlist.push(symbol);

    saveWatchlist();
    loadWatchlist();

    if (message) {
        message.textContent =
            `${symbol} was added to your watchlist.`;
    }
}


function removeFromWatchlist(symbol) {
    watchlist = watchlist.filter(
        item => item !== symbol
    );

    saveWatchlist();
    loadWatchlist();
}


function handleWatchlistClick(event) {
    const button = event.target.closest(
        "button[data-action]"
    );

    if (!button) {
        return;
    }

    const action = button.dataset.action;
    const symbol = button.dataset.symbol;

    if (!symbol) {
        return;
    }

    if (action === "select") {
        selectWatchStock(symbol);
    }

    if (action === "remove") {
        removeFromWatchlist(symbol);
    }
}


window.addEventListener("load", () => {
    const container = document.getElementById("watchlist");

    if (container) {
        container.addEventListener(
            "click",
            handleWatchlistClick
        );
    }

    loadWatchlist();
});