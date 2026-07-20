let tradeInProgress = false;
let quoteRefreshTimer = null;
let quoteRequestNumber = 0;


document.addEventListener("DOMContentLoaded", () => {
    const symbolInput = document.getElementById("symbol");

    if (!symbolInput) {
        return;
    }

    symbolInput.addEventListener("input", () => {
        clearTimeout(quoteRefreshTimer);

        quoteRefreshTimer = setTimeout(() => {
            loadLivePrice();
        }, 500);
    });

    symbolInput.addEventListener("change", loadLivePrice);
});


async function loadLivePrice() {
    const symbolInput = document.getElementById("symbol");
    const priceDisplay = document.getElementById("price");

    if (!priceDisplay) {
        return;
    }

    const symbol = String(
        symbolInput?.value || ""
    ).trim().toUpperCase();

    if (!symbol) {
        priceDisplay.value = "";
        priceDisplay.classList.remove("error");
        return;
    }

    const currentRequest = ++quoteRequestNumber;

    priceDisplay.value = "Loading...";
    priceDisplay.classList.remove("error");

    try {
        const response = await fetch(
            `${API_URL}/quote/${encodeURIComponent(symbol)}`
        );

        let result;

        try {
            result = await response.json();
        } catch {
            result = {};
        }

        if (currentRequest !== quoteRequestNumber) {
            return;
        }

        if (!response.ok) {
            throw new Error(
                result.message ||
                result.error ||
                "Unable to fetch the live price."
            );
        }

        const price = Number(
            result.price ??
            result.current_price
        );

        if (!Number.isFinite(price) || price <= 0) {
            throw new Error(
                "No live price was returned for this symbol."
            );
        }

        priceDisplay.value = price.toFixed(2);

    } catch (error) {
        console.error("Quote error:", error);

        priceDisplay.value = "Unavailable";
        priceDisplay.classList.add("error");
    }
}


async function buyStock() {
    await submitTrade("buy");
}


async function sellStock() {
    await submitTrade("sell");
}


async function submitTrade(action) {
    if (tradeInProgress) {
        return;
    }

    const symbolInput =
        document.getElementById("symbol");

    const sharesInput =
        document.getElementById("shares");

    const symbol = String(
        symbolInput?.value || ""
    ).trim().toUpperCase();

    const shares = Number(
        sharesInput?.value
    );

    if (
        !symbol ||
        !Number.isFinite(shares) ||
        !Number.isInteger(shares) ||
        shares <= 0
    ) {
        showTradeMessage(
            "Please enter a valid stock symbol and a whole number of shares.",
            "error"
        );

        return;
    }

    tradeInProgress = true;
    setTradeButtonsDisabled(true);

    const actionLabel =
        action === "buy"
            ? "Buying"
            : "Selling";

    showTradeMessage(
        `${actionLabel} ${shares} share${shares === 1 ? "" : "s"} of ${symbol} at the current market price...`,
        ""
    );

    try {
        const response = await fetch(
            `${API_URL}/${action}`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    symbol,
                    shares
                })
            }
        );

        let result;

        try {
            result = await response.json();
        } catch {
            result = {};
        }

        if (!response.ok) {
            throw new Error(
                result.message ||
                result.error ||
                `Trade request failed: ${response.status}`
            );
        }

        if (
            result.success === false ||
            result.error
        ) {
            throw new Error(
                result.message ||
                result.error ||
                "The trade could not be completed."
            );
        }

        showTradeMessage(
            result.message ||
            `${action === "buy" ? "Buy" : "Sell"} order completed.`,
            "success"
        );

        if (sharesInput) {
            sharesInput.value = "";
        }

        await refreshAfterTrade(symbol);
        await loadLivePrice();

    } catch (error) {
        console.error(
            "Trade error:",
            error
        );

        showTradeMessage(
            error.message ||
            "Unable to complete the trade.",
            "error"
        );

    } finally {
        tradeInProgress = false;
        setTradeButtonsDisabled(false);
    }
}


async function refreshAfterTrade(symbol) {
    const refreshTasks = [];

    if (typeof loadAccount === "function") {
        refreshTasks.push(
            Promise.resolve(
                loadAccount()
            )
        );
    }

    if (typeof loadWatchlist === "function") {
        refreshTasks.push(
            Promise.resolve(
                loadWatchlist()
            )
        );
    }

    if (typeof loadPortfolioChart === "function") {
        refreshTasks.push(
            Promise.resolve(
                loadPortfolioChart()
            )
        );
    }

    await Promise.allSettled(
        refreshTasks
    );

    if (
        typeof window.loadChart === "function"
    ) {
        window.loadChart(symbol);
    }
}


function showTradeMessage(text, type) {
    const message =
        document.getElementById(
            "trade-message"
        );

    if (!message) {
        return;
    }

    message.textContent = text;

    message.classList.remove(
        "success",
        "error"
    );

    if (type) {
        message.classList.add(type);
    }
}


function setTradeButtonsDisabled(disabled) {
    const buttons =
        document.querySelectorAll(
            "button[onclick='buyStock()'], button[onclick='sellStock()']"
        );

    buttons.forEach(button => {
        button.disabled = disabled;
    });
}