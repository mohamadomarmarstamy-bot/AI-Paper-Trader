let portfolioChart;
let portfolioSeries;

function initializePortfolioChart() {
    const container = document.getElementById("portfolio-chart");

    if (!container) return;

    if (portfolioChart) {
        portfolioChart.remove();
    }

    portfolioChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
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

    window.addEventListener("resize", () => {
        portfolioChart.applyOptions({
            width: container.clientWidth
        });
    });
}

async function loadPortfolioChart() {

    if (!portfolioChart) {
        initializePortfolioChart();
    }

    try {

        const response = await fetch(
            "http://127.0.0.1:8000/portfolio-history"
        );

        const history = await response.json();

        const data = history.map(point => ({
            time: Math.floor(
                new Date(point.time).getTime() / 1000
            ),
            value: point.value
        }));

        portfolioSeries.setData(data);

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