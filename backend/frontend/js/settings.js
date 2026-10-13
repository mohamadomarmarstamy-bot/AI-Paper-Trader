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

    function requestControlToken() {
        return new Promise((resolve) => {
            const dialog = document.getElementById(
                "settings-control-token-dialog"
            );
            const form = document.getElementById(
                "settings-control-token-form"
            );
            const input = document.getElementById(
                "settings-control-token-input"
            );
            const cancel = document.getElementById(
                "settings-control-token-cancel"
            );

            if (
                !dialog ||
                !form ||
                !input ||
                !cancel ||
                typeof dialog.showModal !== "function"
            ) {
                resolve(null);
                return;
            }

            let submitted = false;

            function cleanup() {
                dialog.removeEventListener("close", onClose);
                form.removeEventListener("submit", onSubmit);
                cancel.removeEventListener("click", onCancel);
                input.value = "";
            }

            function onClose() {
                if (!submitted) {
                    cleanup();
                    resolve(null);
                }
            }

            function onCancel() {
                dialog.close();
            }

            function onSubmit(event) {
                event.preventDefault();

                const token = input.value;

                if (!token.trim()) {
                    return;
                }

                submitted = true;
                cleanup();
                dialog.close();
                resolve(token);
            }

            dialog.addEventListener("close", onClose);
            form.addEventListener("submit", onSubmit);
            cancel.addEventListener("click", onCancel);

            input.value = "";
            dialog.showModal();
            input.focus();
        });
    }

    async function savePauseSetting(event) {
        const { toggle, description } = elements();

        if (!toggle || !description) {
            return;
        }

        const requestedValue = toggle.checked;
        const previousValue = !requestedValue;

        toggle.disabled = true;

        const token = await requestControlToken();

        if (token === null || token.trim() === "") {
            toggle.checked = previousValue;
            toggle.disabled = false;
            showStatus("Setting change canceled.");
            return;
        }

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
                        "X-Auto-Trader-Token": token,
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
