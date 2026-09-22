"use strict";

(() => {
    const state = {
        ready: false,
        sending: false,
    };

    function getElements() {
        return {
            form: document.getElementById("jarvis-form"),
            input: document.getElementById("jarvis-input"),
            sendButton: document.getElementById("jarvis-send-button"),
            messages: document.getElementById("jarvis-messages"),
            status: document.getElementById("jarvis-status"),
            statusText: document.getElementById("jarvis-status-text"),
        };
    }

    function setStatus(text, ready = false) {
        const { status, statusText } = getElements();

        state.ready = ready;

        if (statusText) {
            statusText.textContent = text;
        }

        if (status) {
            status.classList.toggle(
                "jarvis-status-online",
                ready
            );
        }
    }

    function addMessage(role, text) {
        const { messages } = getElements();

        if (!messages) {
            return;
        }

        const message = document.createElement("div");
        const author = document.createElement("strong");
        const body = document.createElement("p");

        message.className = [
            "jarvis-message",
            role === "user"
                ? "jarvis-message-user"
                : "jarvis-message-assistant",
        ].join(" ");

        author.textContent = (
            role === "user"
                ? "You"
                : "Jarvis"
        );

        body.textContent = String(text || "");

        message.appendChild(author);
        message.appendChild(body);
        messages.appendChild(message);

        messages.scrollTop = messages.scrollHeight;
    }

    function setSending(sending) {
        const { input, sendButton } = getElements();

        state.sending = sending;

        if (input) {
            input.disabled = sending;
        }

        if (sendButton) {
            sendButton.disabled = sending;
            sendButton.textContent = (
                sending
                    ? "Thinking..."
                    : "Send"
            );
        }
    }

    async function loadStatus() {
        try {
            const response = await fetch(
                "/jarvis/status",
                {
                    credentials: "same-origin",
                }
            );

            if (!response.ok) {
                throw new Error(
                    `Status request failed (${response.status}).`
                );
            }

            const data = await response.json();

            if (data.success && data.chat_ready) {
                setStatus("Online", true);
                return;
            }

            setStatus("Unavailable", false);
        } catch (error) {
            console.error(
                "Jarvis status error:",
                error
            );

            setStatus("Offline", false);
        }
    }

    async function sendMessage(message) {
        const cleanMessage = String(
            message || ""
        ).trim();

        if (!cleanMessage || state.sending) {
            return;
        }

        addMessage("user", cleanMessage);
        setSending(true);

        try {
            const response = await fetch(
                "/jarvis/chat",
                {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        message: cleanMessage,
                    }),
                }
            );

            if (!response.ok) {
                throw new Error(
                    `Chat request failed (${response.status}).`
                );
            }

            const data = await response.json();

            if (!data.success) {
                throw new Error(
                    data.error || "Jarvis could not respond."
                );
            }

            addMessage(
                "assistant",
                data.reply || "No response received."
            );
        } catch (error) {
            console.error(
                "Jarvis chat error:",
                error
            );

            addMessage(
                "assistant",
                `I couldn't complete that request. ${
                    error.message || "Please try again."
                }`
            );
        } finally {
            setSending(false);

            const { input } = getElements();

            if (input) {
                input.focus();
            }
        }
    }

    function bindJarvis() {
        const {
            form,
            input,
        } = getElements();

        if (!form || !input) {
            return;
        }

        form.addEventListener(
            "submit",
            async (event) => {
                event.preventDefault();

                const message = input.value.trim();

                if (!message) {
                    return;
                }

                input.value = "";

                await sendMessage(message);
            }
        );

        input.addEventListener(
            "keydown",
            (event) => {
                if (
                    event.key === "Enter" &&
                    !event.shiftKey
                ) {
                    event.preventDefault();
                    form.requestSubmit();
                }
            }
        );

        loadStatus();
    }

    document.addEventListener(
        "DOMContentLoaded",
        bindJarvis
    );

    window.refreshJarvisStatus = loadStatus;
})();
