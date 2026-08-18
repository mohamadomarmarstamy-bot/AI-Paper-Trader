window.API_URL =
    "https://ai-paper-trader-production-7465.up.railway.app";
    
async function refreshDashboard() {
    try {
        await loadAccount();
    } catch (error) {
        console.error(
            "Dashboard refresh failed:",
            error
        );
    }
}

document.addEventListener(
    "DOMContentLoaded",
    () => {
        refreshDashboard();
    }
);