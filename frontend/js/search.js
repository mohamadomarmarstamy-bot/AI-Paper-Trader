const SEARCH_API_URL = "http://127.0.0.1:8000";

const symbolInput = document.getElementById("symbol");
const priceInput = document.getElementById("price");
const searchResults = document.getElementById("searchResults");

let searchTimer;

symbolInput.addEventListener("input", () => {
    clearTimeout(searchTimer);

    const query = symbolInput.value.trim();

    if (!query) {
        hideSearchResults();
        return;
    }

    searchTimer = setTimeout(() => {
        searchStocks(query);
    }, 350);
});


async function searchStocks(query) {
    try {
        const response = await fetch(
            `${SEARCH_API_URL}/search?query=${encodeURIComponent(query)}`
        );

        if (!response.ok) {
            throw new Error("Stock search failed.");
        }

        const stocks = await response.json();

        showSearchResults(stocks);

    } catch (error) {
        console.error("Search error:", error);
        hideSearchResults();
    }
}


function showSearchResults(stocks) {
    searchResults.innerHTML = "";

    if (!stocks.length) {
        hideSearchResults();
        return;
    }

    stocks.forEach((stock) => {
        const button = document.createElement("button");

        button.type = "button";
        button.className = "search-result";

        button.innerHTML = `
            <strong>${stock.symbol}</strong>
            <span>${stock.name}</span>
        `;

        button.addEventListener("click", async () => {
            await selectStock(stock.symbol);
        });

        searchResults.appendChild(button);
    });

    searchResults.style.display = "block";
}


async function selectStock(symbol) {
    symbolInput.value = symbol;
    hideSearchResults();

    priceInput.value = "";
    priceInput.placeholder = "Loading price...";

    try {
        const response = await fetch(
            `${SEARCH_API_URL}/quote/${encodeURIComponent(symbol)}`
        );

        if (!response.ok) {
            throw new Error("Price request failed.");
        }

        const quote = await response.json();

        if (quote.error) {
            throw new Error(quote.error);
        }

        priceInput.value = Number(quote.price).toFixed(2);
        priceInput.placeholder = "0.00";

        if (typeof loadChart === "function") {
            loadChart(symbol);
        }

    } catch (error) {
        console.error("Quote error:", error);

        priceInput.value = "";
        priceInput.placeholder = "Price unavailable";

        const tradeMessage = document.getElementById("trade-message");

        if (tradeMessage) {
            tradeMessage.textContent =
                `Could not load the latest price for ${symbol}.`;
        }
    }
}


function hideSearchResults() {
    searchResults.style.display = "none";
    searchResults.innerHTML = "";
}


document.addEventListener("click", (event) => {
    const searchWrapper = document.querySelector(".search-wrapper");

    if (searchWrapper && !searchWrapper.contains(event.target)) {
        hideSearchResults();
    }
});