"use strict";

const NOTIFICATION_POLL_MS = 5000;
const NOTIFICATION_LIMIT = 100;
const NOTIFICATION_STORAGE_KEY =
    "ai-paper-trader-last-read-notification";

function getNotificationApiUrl() {
    const apiUrl =
        typeof window.API_URL === "string"
            ? window.API_URL.trim()
            : "";

    return apiUrl.replace(/\/+$/, "");
}

let notificationItems = [];
let notificationTimer = null;
let lastSeenNewestTimestamp = 0;


function notificationApiUrl(path) {
    return `${getNotificationApiUrl()}${path}`;
}


function normalizeNotificationLog(item) {
    const details =
        item &&
        typeof item.details === "object" &&
        item.details
            ? item.details
            : {};

    return {
        timestamp: Number(
            item?.timestamp || 0
        ),
        event: String(
            item?.event || ""
        ).trim(),
        symbol: String(
            item?.symbol || ""
        ).trim().toUpperCase(),
        message: String(
            item?.message || ""
        ).trim(),
        details,
    };
}


function classifyNotification(item) {
    const event =
        item.event.toLowerCase();

    const success =
        item.details?.result_success;

    if (
        event.includes("error") ||
        event === "background_error" ||
        event === "cycle_error"
    ) {
        return "error";
    }

    if (
        event.includes("entry") ||
        event.includes("exit") ||
        event.includes("buy") ||
        event.includes("sell")
    ) {
        return success === false
            ? "blocked"
            : "trade";
    }

    return "system";
}


function notificationTitle(item) {
    const category =
        classifyNotification(item);

    const symbolSuffix =
        item.symbol
            ? ` — ${item.symbol}`
            : "";

    if (category === "trade") {
        if (
            item.event
                .toLowerCase()
                .includes("exit")
        ) {
            return `SELL${symbolSuffix}`;
        }

        return `BUY${symbolSuffix}`;
    }

    if (category === "blocked") {
        return `BLOCKED${symbolSuffix}`;
    }

    if (category === "error") {
        return `ERROR${symbolSuffix}`;
    }

    if (item.event === "enabled") {
        return "AUTO TRADER ENABLED";
    }

    if (item.event === "disabled") {
        return "AUTO TRADER DISABLED";
    }

    return (
        item.event
            .replaceAll("_", " ")
            .toUpperCase() +
        symbolSuffix
    );
}
function notificationIcon(category) {
    if (category === "trade") {
        return "↗";
    }

    if (category === "blocked") {
        return "!";
    }

    if (category === "error") {
        return "×";
    }

    return "•";
}


function formatNotificationTime(timestamp) {
    if (!timestamp) {
        return "Unknown time";
    }

    const date =
        new Date(
            timestamp * 1000
        );

    if (
        Number.isNaN(
            date.getTime()
        )
    ) {
        return "Unknown time";
    }

    return date.toLocaleString(
        [],
        {
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        }
    );
}


function notificationMeta(item) {
    const parts = [];

    if (
        Number.isFinite(
            Number(
                item.details?.shares
            )
        )
    ) {
        parts.push(
            `${item.details.shares} shares`
        );
    }

    if (
        Number.isFinite(
            Number(
                item.details?.score
            )
        )
    ) {
        parts.push(
            `Score ${item.details.score}`
        );
    }

    if (
        Number.isFinite(
            Number(
                item.details?.confidence
            )
        )
    ) {
        parts.push(
            `Confidence ${item.details.confidence}%`
        );
    }

    return parts.join(" • ");
}


function notificationBody(item) {
    const error =
        String(
            item.details?.error || ""
        ).trim();

    if (error) {
        return error;
    }

    return (
        item.message ||
        "Automatic trader event."
    );
}


function escapeNotificationHtml(value) {
    return String(
        value ?? ""
    )
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function getNotificationFilter() {
    return (
        document
            .getElementById(
                "notification-filter"
            )
            ?.value ||
        "all"
    );
}
function renderNotifications() {
    const feed =
        document.getElementById(
            "notification-feed"
        );

    const status =
        document.getElementById(
            "notification-feed-status"
        );

    if (!feed) {
        return;
    }

    const filter =
        getNotificationFilter();

    const filtered =
        notificationItems.filter(
            item => {
                if (filter === "all") {
                    return true;
                }

                return (
                    classifyNotification(
                        item
                    ) === filter
                );
            }
        );

    if (!filtered.length) {
        feed.innerHTML = `
            <div class="notification-empty">
                No ${
                    filter === "all"
                        ? ""
                        : `${escapeNotificationHtml(filter)} `
                }notifications yet.
            </div>
        `;

        if (status) {
            status.textContent =
                notificationItems.length
                    ? `${notificationItems.length} total events`
                    : "Waiting for activity…";
        }

        return;
    }

    feed.innerHTML =
        filtered
            .map(
                item => {
                    const category =
                        classifyNotification(
                            item
                        );

                    const meta =
                        notificationMeta(
                            item
                        );

                    return `
                        <article class="notification-item notification-${category}">
                            <div class="notification-icon" aria-hidden="true">
                                ${notificationIcon(category)}
                            </div>

                            <div class="notification-content">
                                <div class="notification-heading">
                                    <strong>
                                        ${escapeNotificationHtml(notificationTitle(item))}
                                    </strong>

                                    <time>
                                        ${escapeNotificationHtml(formatNotificationTime(item.timestamp))}
                                    </time>
                                </div>

                                ${
                                    meta
                                        ? `<p class="notification-meta">${escapeNotificationHtml(meta)}</p>`
                                        : ""
                                }

                                <p class="notification-message">
                                    ${escapeNotificationHtml(notificationBody(item))}
                                </p>
                            </div>
                        </article>
                    `;
                }
            )
            .join("");

    if (status) {
        status.textContent =
            `${notificationItems.length} event${
                notificationItems.length === 1
                    ? ""
                    : "s"
            } loaded`;
    }
}


function getLastReadTimestamp() {
    const value =
        Number(
            window.localStorage.getItem(
                NOTIFICATION_STORAGE_KEY
            ) || 0
        );

    return Number.isFinite(value)
        ? value
        : 0;
}


function setLastReadTimestamp(
    timestamp
) {
    const safe =
        Number(
            timestamp || 0
        );

    if (
        !Number.isFinite(safe)
    ) {
        return;
    }

    window.localStorage.setItem(
        NOTIFICATION_STORAGE_KEY,
        String(safe)
    );
}


function updateNotificationBadge() {
    const badge =
        document.getElementById(
            "notification-badge"
        );

    if (!badge) {
        return;
    }

    const lastRead =
        getLastReadTimestamp();

    const unread =
        notificationItems.filter(
            item =>
                item.timestamp >
                lastRead
        ).length;

    badge.textContent =
        String(unread);

    badge.classList.toggle(
        "hidden",
        unread <= 0
    );
}


function markAllNotificationsRead() {
    const newest =
        notificationItems.reduce(
            (
                maxValue,
                item
            ) =>
                Math.max(
                    maxValue,
                    item.timestamp || 0
                ),
            0
        );

    if (newest > 0) {
        setLastReadTimestamp(
            newest
        );
    }

    updateNotificationBadge();
}
async function loadNotifications(
    options = {}
) {
    const status =
        document.getElementById(
            "notification-feed-status"
        );

    try {
        if (
            status &&
            options.force
        ) {
            status.textContent =
                "Refreshing…";
        }

        const response =
            await fetch(
                notificationApiUrl(
                    `/auto-trader/logs?limit=${NOTIFICATION_LIMIT}`
                ),
                {
                    method: "GET",
                    cache: "no-store",
                }
            );

        if (!response.ok) {
            throw new Error(
                `HTTP ${response.status}`
            );
        }

        const payload =
            await response.json();

        const logs =
            Array.isArray(
                payload?.logs
            )
                ? payload.logs
                : [];

        notificationItems =
            logs
                .map(
                    normalizeNotificationLog
                )
                .filter(
                    item =>
                        item.timestamp >
                        0
                );

        const newestTimestamp =
            notificationItems[0]
                ?.timestamp || 0;

        if (
            lastSeenNewestTimestamp >
                0 &&
            newestTimestamp >
                lastSeenNewestTimestamp
        ) {
            document.dispatchEvent(
                new CustomEvent(
                    "notifications:new",
                    {
                        detail: {
                            newestTimestamp,
                        },
                    }
                )
            );
        }

        lastSeenNewestTimestamp =
            Math.max(
                lastSeenNewestTimestamp,
                newestTimestamp
            );

        renderNotifications();

        if (options.markRead) {
            markAllNotificationsRead();
        } else {
            updateNotificationBadge();
        }
    } catch (error) {
        console.warn(
            "Notification feed failed:",
            error
        );

        if (status) {
            status.textContent =
                "Notification feed unavailable";
        }
    }
}


function startNotificationPolling() {
    if (notificationTimer) {
        window.clearInterval(
            notificationTimer
        );
    }

    notificationTimer =
        window.setInterval(
            () =>
                loadNotifications(),
            NOTIFICATION_POLL_MS
        );
}


document.addEventListener(
    "DOMContentLoaded",
    () => {
        document
            .getElementById(
                "notification-filter"
            )
            ?.addEventListener(
                "change",
                renderNotifications
            );

        loadNotifications();
        startNotificationPolling();
    }
);


window.loadNotifications =
    loadNotifications;

window.markAllNotificationsRead =
    markAllNotificationsRead;