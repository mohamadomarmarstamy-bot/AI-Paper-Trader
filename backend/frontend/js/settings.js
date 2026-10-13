"use strict";

(function () {
    const API_BASE = window.API_URL || window.location.origin;

    function elements() {
        return {
            toggle: document.getElementById(
                "settings-pause-new-entries"
            ),
            description: document.getElementById(
                "settings-pause-new-entries-description"
            ),
            status: document.getElementById(
                "settings-pause-new-entries-status"
            ),
        };
    }

    function showStatus(message) {
        const { status } = elements();

        if (status) {
            status.textContent = message;
        }
    }

    async function loadPauseSetting() {
        const { toggle, description } = elements();

        if (!toggle || !description) {
            return;
        }

        toggle.disabled = true;
        description.textContent = "Loading saved setting...";

        try {
            const response = await fetch(
                `${API_BASE}/auto-trader/settings`,
                {
                    method: "GET",
                    credentials: "same-origin",
                    headers: {
                        Accept: "application/json",
                    },
                    cache: "no-store",
                }
            );

            if (!response.ok) {
                throw new Error(
                    `HTTP ${response.status}`
                );
            }

            const data = await response.json();

            if (
                data.success !== true ||
                typeof data.settings?.pause_new_entries !== "boolean"
            ) {
                throw new Error(
                    "Invalid settings response."
                );
            }

            toggle.checked = data.settings.pause_new_entries;

            description.textContent = toggle.checked
                ? "Paused: new automatic paper BUYs are blocked."
                : "Active: new automatic paper BUYs are permitted by settings.";

            toggle.disabled = false;
            showStatus("");
        } catch (error) {
            toggle.disabled = true;

            description.textContent =
                "Unable to load saved setting.";

            showStatus(
                `Settings unavailable: ${error.message}`
            );
        }
    }

    async function savePauseSetting(event) {
        const { toggle, description } = elements();

        if (!toggle || !description) {
            return;
        }

        const requestedValue = toggle.checked;
        const previousValue = !requestedValue;

        toggle.disabled = true;

        showStatus("Saving setting...");

        try {
            const response = await fetch(
                `${API_BASE}/auto-trader/settings`,
                {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "application/json",
                    },
                    body: JSON.stringify({
                        pause_new_entries: requestedValue,
                    }),
                }
            );

            if (!response.ok) {
                throw new Error(
                    `HTTP ${response.status}`
                );
            }

            const data = await response.json();

            if (
                data.success !== true ||
                data.settings?.pause_new_entries !== requestedValue
            ) {
                throw new Error(
                    data.error || "Setting was not saved."
                );
            }

            description.textContent = requestedValue
                ? "Paused: new automatic paper BUYs are blocked."
                : "Active: new automatic paper BUYs are permitted by settings.";

            showStatus(
                requestedValue
                    ? "New automatic paper entries paused successfully."
                    : "New automatic paper entries resumed successfully."
            );
        } catch (error) {
            toggle.checked = previousValue;

            showStatus(
                `Could not save setting: ${error.message}`
            );
        } finally {
            toggle.disabled = false;
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        const { toggle } = elements();

        if (!toggle) {
            return;
        }

        toggle.addEventListener(
            "change",
            savePauseSetting
        );

        loadPauseSetting();
    });
})();
