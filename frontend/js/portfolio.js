let portfolioChart = null;
let portfolioSeries = null;
let portfolioResizeObserver = null;

function initializePortfolioChart() {
    const container = document.getElementById("portfolio-chart");

    if (!container) {
        console.error("Portfolio chart container was not found.");
        return;
    }

    if (portfolioResizeObserver) {
        portfolioResizeObserver.disconnect();
        portfolioResizeObserver = null;
    }

    if (portfolioChart) {
        portfolioChart.remove();
        portfolioChart = null;
        portfolioSeries = null;
    }

    const width = Math.max(container.clientWidth, 300);

    portfolioChart = LightweightCharts.createChart(container, {
        width: width,
        height: 350,

        layout: {
            background: {
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
            timeVisible: true,
            secondsVisible: true
        }
    });

    portfolioSeries = portfolioChart.addSeries(
        LightweightCharts.LineSeries,
        {
            lineWidth: 3,
            priceLineVisible: false,
            lastValueVisible: true
        }
    );

    portfolioResizeObserver = new ResizeObserver(entries => {
        if (!portfolioChart || !container.isConnected) {
            return;
        }

        const entry = entries[0];

        if (!entry) {
            return;
        }

        const newWidth = Math.floor(entry.contentRect.width);

        if (newWidth > 0) {
            portfolioChart.resize(newWidth, 350);
        }
    });

    portfolioResizeObserver.observe(container);
}

async function loadPortfolioChart() {
    if (!portfolioChart || !portfolioSeries) {
        initializePortfolioChart();
    }

    if (!portfolioChart || !portfolioSeries) {
        return;
    }

    try {
        const response = await fetch(
            "https://ai-paper-trader-production-7465.up.railway.app/portfolio-history"
        );

        if (!response.ok) {
            throw new Error(
                `Portfolio history request failed: ${response.status}`
            );
        }

        const history = await response.json();

        if (!Array.isArray(history)) {
            console.error(
                "Portfolio history must be an array:",
                history
            );

            portfolioSeries.setData([]);
            return;
        }

        const data = history
            .filter(point => {
                return (
                    point &&
                    point.time &&
                    point.value !== null &&
                    point.value !== undefined
                );
            })
            .map(point => ({
                time: Math.floor(
                    new Date(point.time).getTime() / 1000
                ),
                value: Number(point.value)
            }))
            .filter(point => {
                return (
                    Number.isFinite(point.time) &&
                    Number.isFinite(point.value)
                );
            })
            .sort((a, b) => a.time - b.time);

        portfolioSeries.setData(data);

        if (data.length > 0) {
            portfolioChart.timeScale().fitContent();
        }
    } catch (error) {
        console.error(
            "Portfolio chart error:",
            error
        );
    }
}

document.addEventListener(
    "DOMContentLoaded",
    loadPortfolioChart
);