const API_URL = "https://ai-paper-trader-production-7465.up.railway.app";


async function refreshDashboard() {
    await loadAccount();
}


document.addEventListener("DOMContentLoaded", async () => {
    await refreshDashboard();
});