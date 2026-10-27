"use strict";

const NOTIFICATION_POLL_MS = 15000;
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
    return item.event === "buy_fill" || item.event === "sell_fill" ? "trade" : "system";
}


function notificationTitle(item) {
    const category =
        classifyNotification(item);

    const symbolSuffix =
        item.symbol
            ? ` — ${item.symbol}`
            : "";

    if (category === "trade") {
        const event =
            item.event.toLowerCase();

        if (
            event.includes("exit") ||
            event.includes("sell")
        ) {
            return `SELL${symbolSuffix}`;
        }

        if (
            event.includes("entry") ||
            event.includes("buy")
        ) {
            return `BUY${symbolSuffix}`;
        }

        return `TRADE${symbolSuffix}`;
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

                return item.event === `${filter}_fill`;
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
                    ? `${notificationItems.length} confirmed fills`
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
            `${notificationItems.length} confirmed fill${
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

        const payload = await window.loadTradeActivity({force:Boolean(options.force)});
        notificationItems = window.appSettings?.notify_trade_events === false ? [] :
            [...payload.buys, ...payload.sells].map(fill => normalizeNotificationLog({
                timestamp: fill.timestamp,
                event: fill.side === "BUY" ? "buy_fill" : "sell_fill",
                symbol: fill.symbol,
                message: `${fill.side === "BUY" ? "Bought" : "Sold"} ${fill.shares} shares at ${new Intl.NumberFormat("en-US", {style:"currency", currency:"USD", maximumFractionDigits:fill.price < 1 ? 4 : 2}).format(fill.price)} per share. Confirmed PAPER fill.`,
                details: {shares:fill.shares, order_id:fill.order_id},
            })).sort((a,b) => b.timestamp-a.timestamp).slice(0, NOTIFICATION_LIMIT);

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
                "Notification feed unavailable; displayed fills may be out of date";
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
document.addEventListener("app:preferences",()=>loadNotifications());
