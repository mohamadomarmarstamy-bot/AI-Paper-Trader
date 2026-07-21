// Show the selected section
function showSection(sectionId) {
    document.querySelectorAll("main, .strategy-section").forEach(section => {
        section.classList.add("hidden");
    });

    document.getElementById(sectionId).classList.remove("hidden");

    document.querySelectorAll(".nav-button").forEach(button => {
        button.classList.remove("active");
    });

    document
        .querySelector(`[data-section="${sectionId}"]`)
        .classList.add("active");
}

// Set up navigation
document.querySelectorAll(".nav-button").forEach(button => {
    button.addEventListener("click", () => {
        showSection(button.dataset.section);
    });
});

// Placeholder for future AI analysis
async function loadStrategyLab() {
    const table = document.getElementById("strategy-table");

    if (!table) {
        console.error("Strategy table was not found.");
        return;
    }

    if (!watchlist || watchlist.length === 0) {
        table.innerHTML = `
            <tr>
                <td colspan="5">Your watchlist is empty.</td>
            </tr>
        `;
        return;
    }

    table.innerHTML = `
        <tr>
            <td colspan="5">Analyzing watchlist...</td>
        </tr>
    `;

    const rows = await Promise.all(
        watchlist.map(async (symbol) => {
            try {
                const response = await fetch(
                    `https://ai-paper-trader-production-7465.up.railway.app/quote/${symbol}`
                );

                if (!response.ok) {
                    throw new Error(`Request failed: ${response.status}`);
                }

                const data = await response.json();
                const price = Number(data.price);

                const signalData = createTemporarySignal(symbol, price);

                return `
                    <tr>
                        <td>${symbol}</td>
                        <td>$${price.toFixed(2)}</td>
                        <td>${signalData.signal}</td>
                        <td>${signalData.confidence}%</td>
                        <td>Watching</td>
                    </tr>
                `;
            } catch (error) {
                console.error(`Could not analyze ${symbol}:`, error);

                return `
                    <tr>
                        <td>${symbol}</td>
                        <td>Unavailable</td>
                        <td>⚪ UNKNOWN</td>
                        <td>--</td>
                        <td>Error</td>
                    </tr>
                `;
            }
        })
    );

    table.innerHTML = rows.join("");
}

function createTemporarySignal(symbol, price) {
    const lastDigit = Math.floor(price * 100) % 10;

    if (lastDigit <= 3) {
        return {
            signal: "🟢 BUY",
            confidence: 72
        };
    }

    if (lastDigit <= 6) {
        return {
            signal: "🟡 HOLD",
            confidence: 64
        };
    }

    return {
        signal: "🔴 SELL",
        confidence: 76
    };
}

// Show the dashboard when the page first loads
showSection("dashboard-section");

// Make function available to HTML buttons
window.loadStrategyLab = loadStrategyLab;