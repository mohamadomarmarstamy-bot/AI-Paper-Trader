let chart = null;
let candleSeries = null;
let volumeSeries = null;
let ma20Series = null;
let ma50Series = null;

let chartContainer = null;
let resizeObserver = null;
let activeChartRequest = null;
let latestRequestNumber = 0;


function createChart() {
    // Never create the chart twice.
    if (chart) {
        return true;
    }

    chartContainer = document.querySelector(".chart-container");

    if (!chartContainer) {
        console.error("Chart container was not found.");
        return false;
    }

    chartContainer.innerHTML = "";

    const chartDiv = document.createElement("div");

    chartDiv.id = "trading-chart";
    chartDiv.style.width = "100%";
    chartDiv.style.height = "420px";

    chartContainer.appendChild(chartDiv);

    chart = LightweightCharts.createChart(chartDiv, {
        width: chartDiv.clientWidth,
        height: 420,

        layout: {
            background: {
                type: "solid",
                color: "#111827"
            },
            textColor: "#d1d5db"
        },

        grid: {
            vertLines: {
                color: "#1f2937"
            },
            horzLines: {
                color: "#1f2937"
            }
        },

        rightPriceScale: {
            borderColor: "#374151"
        },

        timeScale: {
            borderColor: "#374151",
            timeVisible: false,
            secondsVisible: false
        },

        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal
        }
    });

    candleSeries = chart.addSeries(
        LightweightCharts.CandlestickSeries,
        {
            upColor: "#22c55e",
            downColor: "#ef4444",
            borderVisible: false,
            wickUpColor: "#22c55e",
            wickDownColor: "#ef4444"
        }
    );

    volumeSeries = chart.addSeries(
        LightweightCharts.HistogramSeries,
        {
            priceFormat: {
                type: "volume"
            },
            priceScaleId: "volume",
            priceLineVisible: false,
            lastValueVisible: false
        }
    );

    chart.priceScale("volume").applyOptions({
        scaleMargins: {
            top: 0.8,
            bottom: 0
        }
    });

    ma20Series = chart.addSeries(
        LightweightCharts.LineSeries,
        {
            color: "#3b82f6",
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            title: "MA 20"
        }
    );

    ma50Series = chart.addSeries(
        LightweightCharts.LineSeries,
        {
            color: "#f59e0b",
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            title: "MA 50"
        }
    );

    resizeObserver = new ResizeObserver((entries) => {
        if (!chart || entries.length === 0) {
            return;
        }

        const width = Math.floor(
            entries[0].contentRect.width
        );

        if (width > 0) {
            chart.applyOptions({
                width: width
            });
        }
    });

    resizeObserver.observe(chartDiv);

    return true;
}


async function loadChart(symbol) {
    const cleanSymbol = String(symbol || "")
        .trim()
        .toUpperCase();

    if (!cleanSymbol) {
        return;
    }

    const chartReady = createChart();

    if (!chartReady) {
        return;
    }

    // Cancel the previous request when another stock is clicked.
    if (activeChartRequest) {
        activeChartRequest.abort();
    }

    activeChartRequest = new AbortController();

    const requestNumber = ++latestRequestNumber;

    try {
        const response = await fetch(
            `http://127.0.0.1:8000/chart/${encodeURIComponent(cleanSymbol)}`,
            {
                signal: activeChartRequest.signal
            }
        );

        if (!response.ok) {
            throw new Error(
                `Chart request failed: ${response.status}`
            );
        }

        const data = await response.json();

        // Ignore an older request that finished late.
        if (requestNumber !== latestRequestNumber) {
            return;
        }

        if (!Array.isArray(data.candles)) {
            throw new Error("Invalid candle data received.");
        }

        candleSeries.setData(data.candles);
        volumeSeries.setData(data.volume || []);
        ma20Series.setData(data.ma20 || []);
        ma50Series.setData(data.ma50 || []);

        chart.timeScale().fitContent();

    } catch (error) {
        if (error.name === "AbortError") {
            return;
        }

        console.error(
            `Could not load chart for ${cleanSymbol}:`,
            error
        );
    }
}


// Make it reliably available to every other JavaScript file.
window.loadChart = loadChart;


window.addEventListener("DOMContentLoaded", () => {
    createChart();
});