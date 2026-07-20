const API_URL = "https://ai-paper-trader-production-7465.up.railway.app";

async function refreshDashboard() {
    await loadAccount();
}

async function loadAccount() {

    const response = await fetch(`${API_URL}/account`);
    const account = await response.json();

    document.getElementById("cash").textContent =
        `$${account.cash.toFixed(2)}`;

    document.getElementById("position-count").textContent =
        account.positions.length;

    let portfolioValue = account.cash;

    const positionsTable =
        document.getElementById("positions-table");

    positionsTable.innerHTML = "";

    if (account.positions.length === 0) {

        positionsTable.innerHTML =
            `<tr><td colspan="4">No positions</td></tr>`;

    } else {

        account.positions.forEach(position => {

            const value =
                position.shares * position.entry;

            portfolioValue += value;

            positionsTable.innerHTML += `
                <tr>
                    <td>${position.symbol}</td>
                    <td>${position.shares}</td>
                    <td>$${position.entry.toFixed(2)}</td>
                    <td>$${value.toFixed(2)}</td>
                </tr>
            `;
        });
    }

    document.getElementById("portfolio-value").textContent =
        `$${portfolioValue.toFixed(2)}`;

    document.getElementById("profit-loss").textContent =
        "$0.00";

    const historyTable =
        document.getElementById("history-table");

    historyTable.innerHTML = "";

    if (account.history.length === 0) {

        historyTable.innerHTML =
            `<tr><td colspan="4">No trades</td></tr>`;

    } else {

        account.history.forEach(trade => {

            historyTable.innerHTML += `
                <tr>
                    <td>${trade.action}</td>
                    <td>${trade.symbol}</td>
                    <td>${trade.shares ?? "-"}</td>
                    <td>$${(trade.price ?? trade.profit).toFixed(2)}</td>
                </tr>
            `;
        });
    }

}

async function loadScanner() {

    const response =
        await fetch(`${API_URL}/scanner`);

    const stocks =
        await response.json();

    const scanner =
        document.getElementById("scanner-results");

    scanner.innerHTML = "";

    stocks.forEach(stock => {

        scanner.innerHTML += `
<div class="scanner-card">

    <div>

        <strong>${stock.symbol}</strong>

        <br>

        <small>$${stock.price}</small>

        <br><br>

        ${stock.signals.map(signal =>
            `<div style="font-size:13px;color:#94a3b8;">
                ✔ ${signal}
            </div>`
        ).join("")}

    </div>

    <div style="text-align:right">

        <div class="scanner-score">
            ${stock.score}/100
        </div>

        <div class="${
            stock.change >= 0
                ? "positive"
                : "negative"
        }">

            ${stock.change >= 0 ? "+" : ""}
            ${stock.change}%

        </div>

    </div>

</div>
`;
    });

}

async function buyStock() {

    const symbol =
        document.getElementById("symbol").value.toUpperCase();

    const shares =
        Number(document.getElementById("shares").value);

    const price =
        Number(document.getElementById("price").value);

    const response =
        await fetch(`${API_URL}/buy`, {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                symbol,
                shares,
                price
            })

        });

    const result =
        await response.json();

    document.getElementById("trade-message").textContent =
        JSON.stringify(result);

    refreshDashboard();

}

async function sellStock() {

    const symbol =
        document.getElementById("symbol").value.toUpperCase();

    const price =
        Number(document.getElementById("price").value);

    const response =
        await fetch(`${API_URL}/sell`, {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                symbol,
                shares: 0,
                price
            })

        });

    const result =
        await response.json();

    document.getElementById("trade-message").textContent =
        JSON.stringify(result);

    refreshDashboard();

}

refreshDashboard();