"use strict";

(function () {
    const API_BASE = window.API_URL || window.location.origin;

    const NUMERIC_SETTINGS = [
        "position_size_percent",
        "max_order_size_percent",
        "max_position_size_percent",
        "max_open_positions",
        "stop_loss_percent",
        "take_profit_percent",
        "profit_lock_trigger_percent",
        "profit_lock_percent",
        "trailing_stop_percent",
        "daily_profit_target_percent",
        "daily_loss_limit_percent",
    ];

    const INPUT_IDS = {
        position_size_percent: "setting-position-size-percent",
        max_order_size_percent: "setting-max-order-size-percent",
        max_position_size_percent: "setting-max-position-size-percent",
        max_open_positions: "setting-max-open-positions",
        stop_loss_percent: "setting-stop-loss-percent",
        take_profit_percent: "setting-take-profit-percent",
        profit_lock_trigger_percent: "setting-profit-lock-trigger-percent",
        profit_lock_percent: "setting-profit-lock-percent",
        trailing_stop_percent: "setting-trailing-stop-percent",
        daily_profit_target_percent: "setting-daily-profit-target-percent",
        daily_loss_limit_percent: "setting-daily-loss-limit-percent",
    };

    function element(id) {
        return document.getElementById(id);
    }

    function showPauseStatus(message) {
        const status = element("settings-pause-new-entries-status");
        if (status) status.textContent = message;
    }

    function showSaveStatus(message) {
        const status = element("settings-save-status");
        if (status) status.textContent = message;
    }

    async function requestSettings(method, body) {
        const options = {
            method,
            credentials: "same-origin",
            cache: "no-store",
            headers: {
                Accept: "application/json",
            },
        };

        if (body !== undefined) {
            options.headers["Content-Type"] = "application/json";
            options.body = JSON.stringify(body);
        }

        const response = await fetch(
            `${API_BASE}/auto-trader/settings`,
            options
        );

        let data;
        try {
            data = await response.json();
        } catch (_error) {
            throw new Error(`HTTP ${response.status}: invalid server response`);
        }

        if (!response.ok || data.success !== true) {
            throw new Error(
                data.error || data.detail || `HTTP ${response.status}`
            );
        }

        return data;
    }

    function renderSettings(settings) {
        NUMERIC_SETTINGS.forEach((key) => {
            const input = element(INPUT_IDS[key]);
            const value = settings[key];

            if (input && typeof value === "number" && Number.isFinite(value)) {
                input.value = String(value);
            }
        });

        const toggle = element("settings-pause-new-entries");
        const description = element("settings-pause-new-entries-description");

        if (toggle && typeof settings.pause_new_entries === "boolean") {
            toggle.checked = settings.pause_new_entries;
            if (description) {
                description.textContent = toggle.checked
                    ? "Paused: new automatic paper BUYs are blocked."
                    : "Active: new automatic paper BUYs are permitted by settings.";
            }
        }
    }

    async function loadSettings() {
        const toggle = element("settings-pause-new-entries");
        const description = element("settings-pause-new-entries-description");
        const saveButton = element("settings-save-button");

        if (toggle) toggle.disabled = true;
        if (saveButton) saveButton.disabled = true;
        if (description) description.textContent = "Loading saved settings...";

        try {
            const data = await requestSettings("GET");
            renderSettings(data.settings || {});
            showPauseStatus("");
            showSaveStatus("Saved settings loaded.");
            if (toggle) toggle.disabled = false;
            if (saveButton) saveButton.disabled = false;
        } catch (error) {
            if (description) {
                description.textContent = "Unable to load saved settings.";
            }
            showPauseStatus(`Settings unavailable: ${error.message}`);
            showSaveStatus("Settings could not be loaded; values were not changed.");
        }
    }

    async function savePauseSetting() {
        const toggle = element("settings-pause-new-entries");
        const description = element("settings-pause-new-entries-description");
        if (!toggle) return;

        const requestedValue = toggle.checked;
        toggle.disabled = true;
        showPauseStatus("Saving pause setting...");

        try {
            const data = await requestSettings("POST", {
                pause_new_entries: requestedValue,
            });
            const savedPauseValue =
                typeof data.settings?.pause_new_entries === "boolean"
                    ? data.settings.pause_new_entries
                    : requestedValue;
            toggle.checked = savedPauseValue;
            if (description) {
                description.textContent = savedPauseValue
                    ? "Paused: new automatic paper BUYs are blocked."
                    : "Active: new automatic paper BUYs are permitted by settings.";
            }
            showPauseStatus(
                requestedValue
                    ? "New automatic paper entries paused successfully."
                    : "New automatic paper entries resumed successfully."
            );
        } catch (error) {
            toggle.checked = !requestedValue;
            if (description) {
                description.textContent = toggle.checked
                    ? "Paused: new automatic paper BUYs are blocked."
                    : "Active: new automatic paper BUYs are permitted by settings.";
            }
            showPauseStatus(`Could not save pause setting: ${error.message}`);
        } finally {
            toggle.disabled = false;
        }
    }

    async function saveNumericSettings(event) {
        event.preventDefault();

        const form = element("auto-trader-settings-form");
        const saveButton = element("settings-save-button");
        if (!form || !form.reportValidity()) return;

        const values = {};
        for (const key of NUMERIC_SETTINGS) {
            const input = element(INPUT_IDS[key]);
            const value = input ? input.valueAsNumber : NaN;
            if (!Number.isFinite(value)) {
                showSaveStatus(`Enter a valid number for ${key}.`);
                return;
            }
            values[key] = value;
        }

        if (saveButton) saveButton.disabled = true;
        showSaveStatus("Validating and saving trading settings...");

        try {
            const data = await requestSettings("POST", values);
            renderSettings(data.settings || values);
            showSaveStatus(
                "Trading settings saved. New values apply to subsequent decisions; existing protective orders were not automatically canceled."
            );
        } catch (error) {
            showSaveStatus(`Could not save settings: ${error.message}`);
        } finally {
            if (saveButton) saveButton.disabled = false;
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        const toggle = element("settings-pause-new-entries");
        const form = element("auto-trader-settings-form");

        if (toggle) {
            toggle.addEventListener("change", savePauseSetting);
        }
        if (form) {
            form.addEventListener("submit", saveNumericSettings);
        }

        loadSettings();
    });
})();
