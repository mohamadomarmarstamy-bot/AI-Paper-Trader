"use strict";

let portfolioChart = null;
let portfolioSeries = null;
let portfolioResizeObserver = null;
let activePortfolioRequest = null;
let portfolioLastData = [];
let portfolioRetryTimer = null;
let portfolioRetryCount = 0;

const PORTFOLIO_CHART_HEIGHT = 350;
const PORTFOLIO_REQUEST_TIMEOUT_MS = 15_000;


function initializePortfolioChart() {
    const container =
        document.getElementById(
            "portfolio-chart"
        );

    if (!container) {
        console.error(
            "Portfolio chart container was not found."
        );

        return false;
    }

    if (
        typeof window.LightweightCharts ===
        "undefined"
    ) {
        console.error(
            "Lightweight Charts is not loaded."
        );

        showPortfolioMessage(
            "The portfolio chart library could not be loaded."
        );

        return false;
    }

    destroyPortfolioChart();

    clearPortfolioMessage();

    const width = Math.max(
        Math.floor(container.clientWidth),
        300
    );

    try {
        portfolioChart =
            window.LightweightCharts.createChart(
                container,
                {
                    width,
                    height:
                        PORTFOLIO_CHART_HEIGHT,

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
                        timeVisible: true,
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

        portfolioSeries =
            addPortfolioLineSeries(
                portfolioChart
            );

        setupPortfolioResizeObserver(
            container
        );

        return true;
    } catch (error) {
        console.error(
            "Could not initialize the portfolio chart:",
            error
        );

        destroyPortfolioChart();

        showPortfolioMessage(
            "The portfolio chart could not be created.",
            true
        );

        return false;
    }
}


async function loadPortfolioChart() {
    if (
        !portfolioChart ||
        !portfolioSeries
    ) {
        const initialized =
            initializePortfolioChart();

        if (!initialized) {
            return;
        }
    }

    window.clearTimeout(portfolioRetryTimer);
    cancelPortfolioRequest();

    const requestController =
        new AbortController();

    activePortfolioRequest =
        requestController;

    const timeoutId =
        window.setTimeout(
            () => {
                requestController.abort();
            },
            PORTFOLIO_REQUEST_TIMEOUT_MS
        );

    setPortfolioLoadingState();

    try {
        const response = await fetch(
            `${getPortfolioApiUrl()}/portfolio-history`,
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
            await readPortfolioJson(
                response
            );

        if (!response.ok) {
            const message =
                payload?.detail ??
                payload?.error ??
                `Portfolio history request failed with status ${response.status}.`;

            throw new Error(
                String(message)
            );
        }

        if (activePortfolioRequest !== requestController) return;
        const data =
            normalizePortfolioHistory(
                payload
            );

        const firstLoad = portfolioLastData.length === 0;
        portfolioLastData = data;
        portfolioRetryCount = 0;
        portfolioSeries.setData(data);
        updatePortfolioSummary(data,response.headers?.get('X-Portfolio-History-Status')==='stale');

        if (data.length === 0) {
            showPortfolioMessage(
                "No portfolio history is available yet."
            );

            return;
        }

        clearPortfolioMessage();

        if (firstLoad) portfolioChart.timeScale().fitContent();
        if(response.headers?.get('X-Portfolio-History-Status')==='stale'){
            showPortfolioMessage('Broker history is temporarily unavailable. Showing the last successful history; retrying shortly.');
            schedulePortfolioRetry();
        }
    } catch (error) {
        if(activePortfolioRequest !== requestController) return;
        const message=error?.name==='AbortError'?'Portfolio history request timed out.':String(error?.message||'Portfolio history unavailable.');
        showPortfolioMessage(message+(portfolioLastData.length?' Showing the last successful history.':'' )+' Retrying shortly.',true);
        schedulePortfolioRetry();
    } finally {
        window.clearTimeout(timeoutId);
        if(activePortfolioRequest === requestController){activePortfolioRequest=null;clearPortfolioLoadingState();}
    }
}


function addPortfolioLineSeries(
    chartInstance
) {
    const options = {
        lineWidth: 3,
        priceLineVisible: false,
        lastValueVisible: true,
        title: "Paper equity",
        priceFormat:{type:'custom',minMove:.01,formatter:value=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(value)},
    };

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


function normalizePortfolioHistory(
    history
) {
    if (!Array.isArray(history)) {
        throw new Error(
            "Portfolio history must be an array."
        );
    }

    const pointsByTime =
        new Map();

    for (const point of history) {
        if (
            !point ||
            typeof point !==
                "object"
        ) {
            continue;
        }

        const time =
            parsePortfolioTime(
                point.time
            );

        if(point.value===null || point.value===undefined || typeof point.value==='boolean' || String(point.value).trim()==='') continue;
        const value = Number(point.value);

        if (
            !Number.isFinite(time) || time<=0 ||
            !Number.isFinite(value) || value<0
        ) {
            continue;
        }

        pointsByTime.set(
            time,
            {
                time,
                value,
            }
        );
    }

    return Array.from(
        pointsByTime.values()
    ).sort(
        (first, second) =>
            first.time -
            second.time
    );
}


function parsePortfolioTime(value) {
    if (
        typeof value ===
            "number" &&
        Number.isFinite(value)
    ) {
        return value > 10_000_000_000
            ? Math.floor(value / 1000)
            : Math.floor(value);
    }

    if (
        typeof value !==
        "string"
    ) {
        return NaN;
    }

    const cleanValue =
        value.trim();

    if (!cleanValue) {
        return NaN;
    }

    const numericValue =
        Number(cleanValue);

    if (
        Number.isFinite(
            numericValue
        )
    ) {
        return numericValue >
            10_000_000_000
            ? Math.floor(
                numericValue / 1000
            )
            : Math.floor(
                numericValue
            );
    }

    const dateValue =
        Date.parse(cleanValue);

    if (
        !Number.isFinite(
            dateValue
        )
    ) {
        return NaN;
    }

    return Math.floor(
        dateValue / 1000
    );
}


function setupPortfolioResizeObserver(
    container
) {
    if (
        typeof window.ResizeObserver ===
        "undefined"
    ) {
        window.addEventListener(
            "resize",
            resizePortfolioChart
        );

        return;
    }

    portfolioResizeObserver =
        new ResizeObserver(
            entries => {
                if (
                    !portfolioChart ||
                    !container.isConnected
                ) {
                    return;
                }

                const entry =
                    entries[0];

                if (!entry) {
                    return;
                }

                const width =
                    Math.floor(
                        entry.contentRect.width
                    );

                resizePortfolioChart(
                    width
                );
            }
        );

    portfolioResizeObserver.observe(
        container
    );
}


function resizePortfolioChart(
    width = null
) {
    if (!portfolioChart) {
        return;
    }

    const container =
        document.getElementById(
            "portfolio-chart"
        );

    if (!container) {
        return;
    }

    const chartWidth =
        Number.isFinite(width)
            ? width
            : Math.floor(
                container.clientWidth
            );

    if (chartWidth <= 0) {
        return;
    }

    if (
        typeof portfolioChart.resize ===
        "function"
    ) {
        portfolioChart.resize(
            chartWidth,
            PORTFOLIO_CHART_HEIGHT
        );

        return;
    }

    portfolioChart.applyOptions({
        width: chartWidth,
        height:
            PORTFOLIO_CHART_HEIGHT,
    });
}


function setPortfolioLoadingState() {
    const container =
        document.getElementById(
            "portfolio-chart"
        );

    if (!container) {
        return;
    }

    clearPortfolioMessage();

    container.setAttribute(
        "aria-busy",
        "true"
    );

    container.setAttribute(
        "aria-label",
        "Loading portfolio history"
    );
}


function clearPortfolioLoadingState() {
    const container =
        document.getElementById(
            "portfolio-chart"
        );

    if (!container) {
        return;
    }

    container.removeAttribute(
        "aria-busy"
    );

    container.setAttribute('aria-label','Portfolio value over time');
}


function showPortfolioMessage(
    message,
    isError = false
) {
    const container =
        document.getElementById(
            "portfolio-chart"
        );

    if (!container) {
        return;
    }

    clearPortfolioMessage();

    const messageElement =
        document.createElement("div");

    messageElement.className =
        "portfolio-chart-message";

    if (isError) {
        messageElement.classList.add(
            "portfolio-chart-error"
        );
    }

    messageElement.setAttribute(
        "role",
        isError
            ? "alert"
            : "status"
    );

    messageElement.textContent =
        message;

    container.appendChild(
        messageElement
    );
}


function clearPortfolioMessage() {
    const container =
        document.getElementById(
            "portfolio-chart"
        );

    if (!container) {
        return;
    }

    const messages =
        container.querySelectorAll(
            ".portfolio-chart-message"
        );

    messages.forEach(
        message => {
            message.remove();
        }
    );
}


function cancelPortfolioRequest() {
    if (activePortfolioRequest) {
        activePortfolioRequest.abort();
        activePortfolioRequest =
            null;
    }
}


function destroyPortfolioChart() {
    window.clearTimeout(portfolioRetryTimer);
    portfolioLastData=[];
    cancelPortfolioRequest();

    portfolioResizeObserver
        ?.disconnect();

    portfolioResizeObserver =
        null;

    window.removeEventListener(
        "resize",
        resizePortfolioChart
    );

    if (portfolioChart) {
        portfolioChart.remove();
    }

    portfolioChart = null;
    portfolioSeries = null;
}


async function readPortfolioJson(
    response
) {
    try {
        return await response.json();
    } catch {
        return null;
    }
}


function getPortfolioApiUrl() {
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


function getSolidBackgroundType() {
    return (
        window.LightweightCharts
            .ColorType?.Solid ??
        "solid"
    );
}


window.initializePortfolioChart =
    initializePortfolioChart;

window.loadPortfolioChart =
    loadPortfolioChart;

window.destroyPortfolioChart =
    destroyPortfolioChart;


document.addEventListener(
    "DOMContentLoaded",
    () => {
        loadPortfolioChart();
        window.setInterval(()=>{if(!document.hidden && !document.getElementById('dashboard-section')?.hidden && !activePortfolioRequest) loadPortfolioChart();},60000);
        document.addEventListener('visibilitychange',()=>{if(!document.hidden && !document.getElementById('dashboard-section')?.hidden && !activePortfolioRequest) loadPortfolioChart();});
    }
);

function schedulePortfolioRetry(){
    window.clearTimeout(portfolioRetryTimer);
    portfolioRetryTimer=window.setTimeout(()=>{if(!document.hidden && !document.getElementById('dashboard-section')?.hidden) return loadPortfolioChart();},Math.min(60000,15000*2**Math.min(portfolioRetryCount++,2)));
}

function updatePortfolioSummary(data,stale=false){
    const label=document.getElementById('portfolio-chart-summary');if(!label)return;
    if(!data.length){label.textContent='No broker equity snapshots yet.';return;}
    const point=data[data.length-1];
    label.textContent=(stale?'Saved history · ':'')+'Daily PAPER account equity · latest snapshot '+new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}).format(new Date(point.time*1000))+' · '+new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(point.value)+'. Current account value may differ.';
}