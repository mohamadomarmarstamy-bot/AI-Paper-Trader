"use strict";

let chart = null;
let candleSeries = null;
let volumeSeries = null;
let ma20Series = null;
let ma50Series = null;

let chartContainer = null;
let chartElement = null;
let resizeObserver = null;
let activeChartRequest = null;
let latestRequestNumber = 0;

let liveChartTimer = null;
let liveChartSymbol = null;
let liveQuoteRequestInFlight = false;
let latestLiveCandle = null;

const CHART_HEIGHT = 420;
const LIVE_CHART_REFRESH_MS = 1_000;
const CHART_REQUEST_TIMEOUT_MS = 15_000;


/**
 * Creates the trading chart once.
 *
 * @returns {boolean} Whether the chart is ready.
 */
function createChart() {
    if (chart) {
        return true;
    }

    if (
        typeof window.LightweightCharts ===
        "undefined"
    ) {
        console.error(
            "Lightweight Charts is not loaded."
        );

        showChartError(
            "The chart library could not be loaded."
        );

        return false;
    }

    chartContainer =
        document.querySelector(
            ".chart-container"
        );

    if (!chartContainer) {
        console.error(
            "Chart container was not found."
        );

        return false;
    }

    chartContainer.innerHTML = "";

    chartElement =
        document.createElement("div");

    chartElement.id = "trading-chart";
    chartElement.style.width = "100%";
    chartElement.style.height =
        `${CHART_HEIGHT}px`;

    chartContainer.appendChild(
        chartElement
    );

    try {
        chart =
            window.LightweightCharts.createChart(
                chartElement,
                {
                    width:
                        chartElement.clientWidth ||
                        chartContainer.clientWidth ||
                        800,

                    height: CHART_HEIGHT,

                    layout: {
                        background: {
                            type:
                                getSolidBackgroundType(),

                            color: "#111827",
                        },

                        textColor: "#d1d5db",
                    },

                    grid: {
                        vertLines: {
                            color: "#1f2937",
                        },

                        horzLines: {
                            color: "#1f2937",
                        },
                    },

                    rightPriceScale: {
                        borderColor: "#374151",
                    },

                    timeScale: {
                        borderColor: "#374151",
                        timeVisible: false,
                        secondsVisible: false,
                    },

                    crosshair: {
                        mode:
                            window.LightweightCharts
                                .CrosshairMode
                                ?.Normal ?? 0,
                    },
                }
            );

        candleSeries =
            addCandlestickSeries(chart);

        volumeSeries =
            addHistogramSeries(chart);

        ma20Series =
            addLineSeries(
                chart,
                {
                    color: "#3b82f6",
                    lineWidth: 2,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    title: "MA 20",
                }
            );

        ma50Series =
            addLineSeries(
                chart,
                {
                    color: "#f59e0b",
                    lineWidth: 2,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    title: "MA 50",
                }
            );

        chart
            .priceScale("volume")
            .applyOptions({
                scaleMargins: {
                    top: 0.8,
                    bottom: 0,
                },
            });

        setupChartResizeObserver();

        return true;
    } catch (error) {
        console.error(
            "Could not create the trading chart:",
            error
        );

        destroyChart();

        showChartError(
            "The trading chart could not be created."
        );

        return false;
    }
}


/**
 * Loads chart information for a stock symbol.
 *
 * @param {string} symbol
 */
async function loadChart(symbol) {
    const cleanSymbol =
        normalizeSymbol(symbol);

    if (!cleanSymbol) {
        console.warn(
            "A valid symbol is required to load a chart."
        );

        return;
    }

    if (!createChart()) {
        return;
    }

    cancelActiveChartRequest();

    const requestController =
        new AbortController();

    activeChartRequest =
        requestController;

    const requestNumber =
        ++latestRequestNumber;

    const timeoutId =
        window.setTimeout(
            () => {
                requestController.abort();
            },
            CHART_REQUEST_TIMEOUT_MS
        );

    setChartLoadingState(cleanSymbol);

    try {
        const response = await fetch(
            `${getApiUrl()}/chart/${encodeURIComponent(
                cleanSymbol
            )}`,
            {
                method: "GET",

                headers: {
                    Accept: "application/json",
                },

                signal:
                    requestController.signal,
            }
        );

        const payload =
            await readJsonResponse(response);

        if (!response.ok) {
            const message =
                payload?.detail ??
                payload?.error ??
                `Chart request failed with status ${response.status}.`;

            throw new Error(
                String(message)
            );
        }

        if (
            requestNumber !==
            latestRequestNumber
        ) {
            return;
        }

        const chartData =
            normalizeChartData(payload);

        candleSeries.setData(
            chartData.candles
        );

        latestLiveCandle =
            chartData.candles.length > 0
                ? {
                    ...chartData.candles[
                        chartData.candles.length - 1
                    ]
                }
                : null;

        volumeSeries.setData(
            chartData.volume
        );

        ma20Series.setData(
            chartData.ma20
        );

        ma50Series.setData(
            chartData.ma50
        );

        if (
            chartData.candles.length === 0
        ) {
            showChartMessage(
                `No chart data is available for ${cleanSymbol}.`
            );

            return;
        }

        clearChartMessage();

        chart
            .timeScale()
            .fitContent();

        startLiveChartUpdates(
            cleanSymbol
        );
    } catch (error) {
        if (
            error?.name ===
            "AbortError"
        ) {
            if (
                requestNumber ===
                latestRequestNumber
            ) {
                console.warn(
                    `Chart request for ${cleanSymbol} was cancelled or timed out.`
                );

                showChartMessage(
                    `The chart request for ${cleanSymbol} timed out.`
                );
            }

            return;
        }

        console.error(
            `Could not load chart for ${cleanSymbol}:`,
            error
        );

        if (
            requestNumber ===
            latestRequestNumber
        ) {
            clearChartSeries();

            showChartMessage(
                `Could not load chart data for ${cleanSymbol}.`
            );
        }
    } finally {
        window.clearTimeout(
            timeoutId
        );

        if (
            activeChartRequest ===
            requestController
        ) {
            activeChartRequest =
                null;
        }
    }
}

/**
 * Start a lightweight one-second quote refresh.
 *
 * This does not re-download the full six-month chart every second.
 * It polls the much smaller /quote endpoint and updates the newest
 * displayed candle plus the Trade Center price field when present.
 */
function startLiveChartUpdates(symbol) {
    stopLiveChartUpdates();

    liveChartSymbol =
        normalizeSymbol(symbol);

    if (!liveChartSymbol) {
        return;
    }

    updateLiveChartPrice();

    liveChartTimer =
        window.setInterval(
            updateLiveChartPrice,
            LIVE_CHART_REFRESH_MS
        );
}


function stopLiveChartUpdates() {
    if (liveChartTimer) {
        window.clearInterval(
            liveChartTimer
        );

        liveChartTimer = null;
    }

    liveChartSymbol = null;
    liveQuoteRequestInFlight = false;
}


async function updateLiveChartPrice() {
    const symbol =
        liveChartSymbol;

    if (
        !symbol ||
        !candleSeries ||
        liveQuoteRequestInFlight
    ) {
        return;
    }

    liveQuoteRequestInFlight = true;

    try {
        const response = await fetch(
            `${getApiUrl()}/quote/${encodeURIComponent(
                symbol
            )}`,
            {
                method: "GET",

                headers: {
                    Accept: "application/json",
                },

                cache: "no-store",
            }
        );

        const payload =
            await readJsonResponse(
                response
            );

        if (!response.ok) {
            return;
        }

        const price =
            Number(
                payload?.price
            );

        if (
            !Number.isFinite(price) ||
            price <= 0
        ) {
            return;
        }

        updateNewestCandle(
            price
        );

        updateTradePriceField(
            symbol,
            price
        );

        document.dispatchEvent(
            new CustomEvent(
                "market:quote-updated",
                {
                    detail: {
                        symbol,
                        price,
                    },
                }
            )
        );

    } catch (error) {
        console.debug(
            `Live quote unavailable for ${symbol}:`,
            error
        );
    } finally {
        liveQuoteRequestInFlight =
            false;
    }
}


function updateNewestCandle(price) {
    if (
        !latestLiveCandle ||
        !candleSeries
    ) {
        return;
    }

    const open =
        Number(
            latestLiveCandle.open
        );

    const previousHigh =
        Number(
            latestLiveCandle.high
        );

    const previousLow =
        Number(
            latestLiveCandle.low
        );

    const safeOpen =
        Number.isFinite(open)
            ? open
            : price;

    const safeHigh =
        Number.isFinite(previousHigh)
            ? Math.max(
                previousHigh,
                price
            )
            : Math.max(
                safeOpen,
                price
            );

    const safeLow =
        Number.isFinite(previousLow)
            ? Math.min(
                previousLow,
                price
            )
            : Math.min(
                safeOpen,
                price
            );

    latestLiveCandle = {
        ...latestLiveCandle,
        open: safeOpen,
        high: safeHigh,
        low: safeLow,
        close: price,
    };

    candleSeries.update(
        latestLiveCandle
    );
}


function updateTradePriceField(
    symbol,
    price
) {
    const symbolInput =
        document.getElementById(
            "symbol"
        );

    const priceInput =
        document.getElementById(
            "price"
        );

    if (
        !symbolInput ||
        !priceInput
    ) {
        return;
    }

    if (
        normalizeSymbol(
            symbolInput.value
        ) !== symbol
    ) {
        return;
    }

    const nextValue =
        Number(price).toFixed(2);

    if (
        priceInput.value ===
        nextValue
    ) {
        return;
    }

    priceInput.value =
        nextValue;

    priceInput.placeholder =
        "0.00";

    priceInput.classList.remove(
        "error"
    );

    priceInput.removeAttribute(
        "aria-busy"
    );

    /*
     * trades.js already listens for this event on the price field.
     * Dispatching it keeps the trade preview synchronized with the
     * newest displayed quote.
     */
    priceInput.dispatchEvent(
        new Event(
            "input",
            {
                bubbles: true,
            }
        )
    );
}


/**
 * Supports Lightweight Charts version 5 and older versions.
 */
function addCandlestickSeries(
    chartInstance
) {
    const options = {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderVisible: false,
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
    };

    if (
        typeof chartInstance.addSeries ===
            "function" &&
        window.LightweightCharts
            .CandlestickSeries
    ) {
        return chartInstance.addSeries(
            window.LightweightCharts
                .CandlestickSeries,
            options
        );
    }

    if (
        typeof chartInstance
            .addCandlestickSeries ===
        "function"
    ) {
        return chartInstance
            .addCandlestickSeries(
                options
            );
    }

    throw new Error(
        "Candlestick series is not supported by this Lightweight Charts version."
    );
}


function addHistogramSeries(
    chartInstance
) {
    const options = {
        priceFormat: {
            type: "volume",
        },

        priceScaleId: "volume",
        priceLineVisible: false,
        lastValueVisible: false,
    };

    if (
        typeof chartInstance.addSeries ===
            "function" &&
        window.LightweightCharts
            .HistogramSeries
    ) {
        return chartInstance.addSeries(
            window.LightweightCharts
                .HistogramSeries,
            options
        );
    }

    if (
        typeof chartInstance
            .addHistogramSeries ===
        "function"
    ) {
        return chartInstance
            .addHistogramSeries(
                options
            );
    }

    throw new Error(
        "Histogram series is not supported by this Lightweight Charts version."
    );
}


function addLineSeries(
    chartInstance,
    options
) {
    if (
        typeof chartInstance.addSeries ===
            "function" &&
        window.LightweightCharts
            .LineSeries
    ) {
        return chartInstance.addSeries(
            window.LightweightCharts
                .LineSeries,
            options
        );
    }

    if (
        typeof chartInstance
            .addLineSeries ===
        "function"
    ) {
        return chartInstance
            .addLineSeries(
                options
            );
    }

    throw new Error(
        "Line series is not supported by this Lightweight Charts version."
    );
}


function getSolidBackgroundType() {
    return (
        window.LightweightCharts
            .ColorType?.Solid ??
        "solid"
    );
}


function setupChartResizeObserver() {
    if (
        typeof window.ResizeObserver ===
        "undefined"
    ) {
        window.addEventListener(
            "resize",
            resizeChart
        );

        return;
    }

    resizeObserver =
        new ResizeObserver(
            entries => {
                const entry =
                    entries[0];

                if (
                    !entry ||
                    !chart
                ) {
                    return;
                }

                const width =
                    Math.floor(
                        entry.contentRect.width
                    );

                resizeChart(width);
            }
        );

    resizeObserver.observe(
        chartElement
    );
}


function resizeChart(width = null) {
    if (
        !chart ||
        !chartElement
    ) {
        return;
    }
        const chartWidth =
        Number.isFinite(width)
            ? width
            : Math.floor(
                chartElement
                    .clientWidth
            );

    if (chartWidth <= 0) {
        return;
    }

    chart.applyOptions({
        width: chartWidth,
        height: CHART_HEIGHT,
    });
}


function normalizeChartData(payload) {
    if (
        !payload ||
        typeof payload !==
            "object" ||
        Array.isArray(payload)
    ) {
        throw new Error(
            "The server returned invalid chart data."
        );
    }

    if (
        !Array.isArray(
            payload.candles
        )
    ) {
        throw new Error(
            "The server response did not include valid candle data."
        );
    }

    return {
        candles:
            normalizeSeriesData(
                payload.candles
            ),

        volume:
            normalizeSeriesData(
                payload.volume
            ),

        ma20:
            normalizeSeriesData(
                payload.ma20
            ),

        ma50:
            normalizeSeriesData(
                payload.ma50
            ),
    };
}


function normalizeSeriesData(series) {
    if (!Array.isArray(series)) {
        return [];
    }

    return series
        .filter(
            item =>
                item &&
                typeof item ===
                    "object" &&
                item.time !==
                    undefined &&
                item.time !==
                    null
        )
        .sort(
            (first, second) =>
                getTimeValue(
                    first.time
                ) -
                getTimeValue(
                    second.time
                )
        );
}


function getTimeValue(value) {
    if (
        typeof value ===
        "number"
    ) {
        return value;
    }

    if (
        typeof value ===
        "string"
    ) {
        const numericValue =
            Number(value);

        if (
            Number.isFinite(
                numericValue
            )
        ) {
            return numericValue;
        }

        const dateValue =
            Date.parse(value);

        if (
            Number.isFinite(
                dateValue
            )
        ) {
            return dateValue;
        }
    }

    if (
        value &&
        typeof value ===
            "object"
    ) {
        return (
            Number(value.year) *
                10_000 +
            Number(value.month) *
                100 +
            Number(value.day)
        );
    }

    return 0;
}


async function readJsonResponse(
    response
) {
    try {
        return await response.json();
    } catch {
        return null;
    }
}


function getApiUrl() {
    const apiUrl =
        String(
            window.API_URL ?? ""
        ).replace(/\/+$/, "");

    if (!apiUrl) {
        throw new Error(
            "API_URL is not configured."
        );
    }

    return apiUrl;
}


function normalizeSymbol(symbol) {
    return String(
        symbol ?? ""
    )
        .trim()
        .toUpperCase();
}


function cancelActiveChartRequest() {
    if (activeChartRequest) {
        activeChartRequest.abort();
        activeChartRequest = null;
    }
}


function clearChartSeries() {
    candleSeries?.setData([]);
    volumeSeries?.setData([]);
    ma20Series?.setData([]);
    ma50Series?.setData([]);
}


function setChartLoadingState(
    symbol
) {
    clearChartMessage();

    if (
        chartContainer
    ) {
        chartContainer.setAttribute(
            "aria-busy",
            "true"
        );

        chartContainer.setAttribute(
            "aria-label",
            `Loading chart for ${symbol}`
        );
    }
}


function clearChartMessage() {
    if (!chartContainer) {
        return;
    }

    chartContainer.removeAttribute(
        "aria-busy"
    );

    chartContainer.removeAttribute(
        "aria-label"
    );

    const message =
        chartContainer.querySelector(
            ".chart-status-message"
        );

    if (message) {
        message.remove();
    }

    if (chartElement) {
        chartElement.style.display =
            "block";
    }
}
function showChartMessage(message) {
    if (!chartContainer) {
        return;
    }

    clearChartMessage();

    chartContainer.removeAttribute(
        "aria-busy"
    );

    if (chartElement) {
        chartElement.style.display =
            "none";
    }

    const messageElement =
        document.createElement("div");

    messageElement.className =
        "chart-status-message";

    messageElement.setAttribute(
        "role",
        "status"
    );

    messageElement.textContent =
        message;

    chartContainer.appendChild(
        messageElement
    );
}


function showChartError(message) {
    chartContainer =
        chartContainer ??
        document.querySelector(
            ".chart-container"
        );

    if (!chartContainer) {
        return;
    }

    chartContainer.innerHTML = "";

    const errorElement =
        document.createElement("div");

    errorElement.className =
        "chart-status-message chart-error-message";

    errorElement.setAttribute(
        "role",
        "alert"
    );

    errorElement.textContent =
        message;

    chartContainer.appendChild(
        errorElement
    );
}


function destroyChart() {
    stopLiveChartUpdates();
    cancelActiveChartRequest();

    resizeObserver?.disconnect();
    resizeObserver = null;

    window.removeEventListener(
        "resize",
        resizeChart
    );

    if (chart) {
        chart.remove();
    }

    chart = null;
    candleSeries = null;
    volumeSeries = null;
    ma20Series = null;
    ma50Series = null;
    chartElement = null;
    latestLiveCandle = null;
}


window.loadChart = loadChart;
window.createChart = createChart;
window.destroyChart = destroyChart;


window.addEventListener(
    "DOMContentLoaded",
    () => {
        createChart();
    }
);