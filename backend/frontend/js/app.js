window.API_URL =
    "https://ai-paper-trader-production-7465.up.railway.app";
    
async function refreshDashboard() {
    const results = await Promise.allSettled([
        loadAccount(),
        loadAutoTraderHealth(),
    ]);

    for (const result of results) {
        if (result.status === "rejected") {
            console.error(
                "Dashboard refresh failed:",
                result.reason
            );
        }
    }
}


async function loadAutoTraderHealth() {
    const badge = document.getElementById(
        "auto-trader-health-badge"
    );

    const traderStatus = document.getElementById(
        "auto-trader-enabled-status"
    );

    const scannerStatus = document.getElementById(
        "auto-trader-scanner-status"
    );

    const cycleStatus = document.getElementById(
        "auto-trader-cycle-status"
    );

    const message = document.getElementById(
        "auto-trader-health-message"
    );

    const errorElement = document.getElementById(
        "auto-trader-last-error"
    );

    if (
        !badge ||
        !traderStatus ||
        !scannerStatus ||
        !cycleStatus ||
        !message ||
        !errorElement
    ) {
        return;
    }

    try {
        const response = await fetch(
            `${window.API_URL}/auto-trader/status`,
            {
                method: "GET",
                headers: {
                    Accept: "application/json",
                },
                cache: "no-store",
            }
        );

        if (!response.ok) {
            throw new Error(
                `Auto Trader status request failed with HTTP ${response.status}.`
            );
        }

        const status = await response.json();

        const health = String(
            status?.health ?? "unknown"
        ).toLowerCase();

        badge.textContent =
            health.toUpperCase();

        badge.dataset.health = health;

        traderStatus.textContent =
            status?.enabled
                ? "Enabled"
                : "Disabled";

        scannerStatus.textContent =
            status?.last_scan_at
                ? "Active"
                : "Not running";

        cycleStatus.textContent =
            status?.cycle_running
                ? "Running"
                : (
                    status?.last_cycle_result?.success === false
                        ? "Failed"
                        : "Idle"
                );

        if (health === "healthy") {
            message.textContent =
                "Trader and scanner are operating normally.";
        } else if (health === "degraded") {
            message.textContent =
                "The latest automatic trading cycle failed.";
        } else if (health === "stalled") {
            message.textContent =
                "No successful trading cycle has completed within the watchdog limit.";
        } else if (health === "waiting") {
            message.textContent =
                "Waiting for the first successful cycle and scanner update.";
        } else if (health === "disabled") {
            message.textContent =
                "Automatic trading is currently disabled.";
        } else {
            message.textContent =
                "Trader health status is unavailable.";
        }

        const latestError =
            status?.last_cycle_result?.error;

        if (latestError) {
            errorElement.textContent =
                `Latest error: ${latestError}`;

            errorElement.hidden = false;
        } else {
            errorElement.textContent = "";
            errorElement.hidden = true;
        }

    } catch (error) {
        console.error(
            "Auto Trader health load failed:",
            error
        );

        badge.textContent = "UNAVAILABLE";
        badge.dataset.health = "unavailable";

        traderStatus.textContent = "Unknown";
        scannerStatus.textContent = "Unknown";
        cycleStatus.textContent = "Unknown";

        message.textContent =
            "Could not load Auto Trader health.";

        errorElement.textContent =
            String(error?.message ?? error);

        errorElement.hidden = false;
    }
}


window.loadAutoTraderHealth =
    loadAutoTraderHealth;

document.addEventListener(
    "DOMContentLoaded",
    () => {
        refreshDashboard();
    }
);