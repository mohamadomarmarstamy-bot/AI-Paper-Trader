const API_URL = "http://127.0.0.1:8000";


async function refreshDashboard() {
    await loadAccount();
}


document.addEventListener("DOMContentLoaded", async () => {
    await refreshDashboard();
});