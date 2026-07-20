async function loadScanner() {

    const scanner = document.getElementById("scanner-results");

    scanner.innerHTML = "Scanning market...";

    try {

        const response = await fetch(`${API_URL}/scanner`);

        const stocks = await response.json();

        if (!stocks.length) {

            scanner.innerHTML = "No opportunities found.";

            return;

        }

        scanner.innerHTML = "";

        stocks.forEach(stock => {

            scanner.innerHTML += `

            <div
                class="scanner-card"
                onclick="selectStock('${stock.symbol}', ${stock.price})"
                style="cursor:pointer;">

                <div>

                    <strong>${stock.symbol}</strong>

                    <br>

                    <small>$${stock.price}</small>

                    <br><br>

                    ${stock.signals.map(signal => `
                        <div style="font-size:13px;color:#94a3b8;">
                            ✔ ${signal}
                        </div>
                    `).join("")}

                </div>

                <div style="text-align:right;">

                    <div class="scanner-score">
                        ${stock.score}/100
                    </div>

                    <div class="${
                        stock.change >= 0
                            ? "positive"
                            : "negative"
                    }">

                        ${
                            stock.change >= 0
                                ? "+"
                                : ""
                        }${stock.change}%

                    </div>

                </div>

            </div>

            `;

        });

    }

    catch(error){

        console.error(error);

        scanner.innerHTML =
            "Unable to load scanner.";

    }

}


function selectStock(symbol, price){

    document.getElementById("symbol").value = symbol;

    document.getElementById("price").value = price;

    loadChart(symbol);

}