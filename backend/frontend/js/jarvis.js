"use strict";

(() => {
    const state = {
        ready: false,
        sending: false,
        voiceEnabled: false,
        speaking: false,
        listening: false,
        recognition: null,
        voiceSession: false,
        muted: false,
        recognitionStarting: false,
    };

    function getElements() {
        return {
            form: document.getElementById("jarvis-form"),
            input: document.getElementById("jarvis-input"),
            sendButton: document.getElementById("jarvis-send-button"),
            messages: document.getElementById("jarvis-messages"),
            status: document.getElementById("jarvis-status"),
            statusText: document.getElementById("jarvis-status-text"),
            voiceButton: document.getElementById("jarvis-voice-button"),
            listenButton: document.getElementById("jarvis-listen-button"),
            stopButton: document.getElementById("jarvis-stop-button"),
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

    function setSpeaking(speaking) {
        const { status } = getElements();

        state.speaking = speaking;

        document.body.classList.toggle(
            "jarvis-speaking",
            speaking
        );

        if (status) {
            status.classList.toggle(
                "jarvis-status-speaking",
                speaking
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

        document.body.classList.toggle(
            "jarvis-thinking",
            sending
        );
    }

    function updateVoiceControls() {
        const {
            voiceButton,
            listenButton,
            stopButton,
        } = getElements();

        if (voiceButton) {
            voiceButton.textContent = (
                state.voiceEnabled
                    ? "🔊 Voice On"
                    : "🔇 Voice Off"
            );

            voiceButton.setAttribute(
                "aria-pressed",
                String(state.voiceEnabled)
            );
        }

        if (stopButton) {
            stopButton.disabled = !state.speaking;
        }
    }

    function stopSpeaking() {
        if (
            "speechSynthesis" in window
        ) {
            window.speechSynthesis.cancel();
        }

        setSpeaking(false);
        updateVoiceControls();
    }

    function speak(text) {
        if (
            !state.voiceEnabled ||
            !("speechSynthesis" in window)
        ) {
            return;
        }

        const cleanText = String(
            text || ""
        )
            .replace(/```[\s\S]*?```/g, " code block omitted. ")
            .replace(/`([^`]+)`/g, "$1")
            .replace(/[*_#>-]/g, " ")
            .replace(/\s+/g, " ")
            .trim();

        if (!cleanText) {
            return;
        }

        stopSpeaking();

        const utterance =
            new SpeechSynthesisUtterance(
                cleanText
            );

        utterance.rate = 1.02;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;

        const voices =
            window.speechSynthesis.getVoices();

        const preferredVoice =
            voices.find(
                voice =>
                    /en-US/i.test(
                        voice.lang
                    )
            ) ||
            voices.find(
                voice =>
                    /^en/i.test(
                        voice.lang
                    )
            );

        if (preferredVoice) {
            utterance.voice =
                preferredVoice;
        }

        utterance.onstart = () => {
            setSpeaking(true);
            updateVoiceControls();

            if (state.voiceSession) {
                updateVoiceMode(
                    "speaking",
                    "Speaking..."
                );
            }
        };

        utterance.onend = () => {
            setSpeaking(false);
            updateVoiceControls();

            if (
                state.voiceSession &&
                !state.muted
            ) {
                updateVoiceMode(
                    "listening",
                    "Listening..."
                );

                window.setTimeout(
                    () => {
                        if (
                            state.voiceSession &&
                            !state.muted &&
                            !state.sending &&
                            !state.speaking
                        ) {
                            startListening();
                        }
                    },
                    300
                );
            }
        };

        utterance.onerror = () => {
            setSpeaking(false);
            updateVoiceControls();

            if (
                state.voiceSession &&
                !state.muted
            ) {
                updateVoiceMode(
                    "listening",
                    "Listening..."
                );

                window.setTimeout(
                    () => {
                        if (
                            state.voiceSession &&
                            !state.muted &&
                            !state.sending &&
                            !state.speaking
                        ) {
                            startListening();
                        }
                    },
                    300
                );
            }
        };

        window.speechSynthesis.speak(
            utterance
        );

        updateVoiceControls();
    }

    function updateVoiceMode(
        mode,
        caption
    ) {
        const voiceMode =
            document.getElementById(
                "jarvis-voice-mode"
            );
        const status =
            document.getElementById(
                "jarvis-voice-mode-status"
            );
        const captionElement =
            document.getElementById(
                "jarvis-voice-caption"
            );

        if (!voiceMode) {
            return;
        }

        voiceMode.classList.remove(
            "listening",
            "thinking",
            "speaking"
        );

        if (mode) {
            voiceMode.classList.add(mode);
        }

        if (status) {
            status.textContent =
                caption || "Ready";
        }

        if (captionElement) {
            captionElement.textContent =
                caption || "Ready";
        }
    }

    function startVoiceSession() {
        const voiceMode =
            document.getElementById(
                "jarvis-voice-mode"
            );

        if (
            !voiceMode ||
            !state.recognition
        ) {
            setStatus(
                "Microphone unavailable",
                state.ready
            );
            return;
        }

        state.voiceSession = true;
        state.muted = false;
        state.voiceEnabled = true;

        voiceMode.classList.add("active");
        voiceMode.classList.remove("muted");

        voiceMode.setAttribute(
            "aria-hidden",
            "false"
        );

        const muteButton =
            document.getElementById(
                "jarvis-voice-mute"
            );

        if (muteButton) {
            muteButton.textContent = "Mute";
            muteButton.setAttribute(
                "aria-pressed",
                "false"
            );
        }

        updateVoiceControls();
        updateVoiceMode(
            "listening",
            "Listening..."
        );

        startListening();
    }

    function endVoiceSession() {
        const voiceMode =
            document.getElementById(
                "jarvis-voice-mode"
            );

        state.voiceSession = false;
        state.muted = false;

        stopListening();
        stopSpeaking();

        voiceMode?.classList.remove(
            "active",
            "listening",
            "thinking",
            "speaking",
            "muted"
        );

        voiceMode?.setAttribute(
            "aria-hidden",
            "true"
        );

        updateVoiceControls();
    }

    function toggleVoiceMute() {
        if (!state.voiceSession) {
            return;
        }

        const voiceMode =
            document.getElementById(
                "jarvis-voice-mode"
            );
        const muteButton =
            document.getElementById(
                "jarvis-voice-mute"
            );

        state.muted = !state.muted;

        voiceMode?.classList.toggle(
            "muted",
            state.muted
        );

        if (muteButton) {
            muteButton.textContent =
                state.muted
                    ? "Unmute"
                    : "Mute";

            muteButton.setAttribute(
                "aria-pressed",
                String(state.muted)
            );
        }

        if (state.muted) {
            stopListening();

            updateVoiceMode(
                null,
                "Muted"
            );
        } else {
            updateVoiceMode(
                "listening",
                "Listening..."
            );

            startListening();
        }
    }
    function setListening(listening) {
        const {
            listenButton,
            statusText,
        } = getElements();

        state.listening = listening;

        document.body.classList.toggle(
            "jarvis-listening",
            listening
        );

        if (listenButton) {
            listenButton.textContent = (
                listening
                    ? "Listening..."
                    : "Listen"
            );

            listenButton.setAttribute(
                "aria-pressed",
                String(listening)
            );
        }

        if (
            statusText &&
            listening
        ) {
            statusText.textContent =
                "Listening...";
        }
    }

    function stopListening() {
        if (
            state.recognition &&
            state.listening
        ) {
            state.recognition.stop();
        }

        setListening(false);
    }

    function startListening() {
        if (!state.recognition) {
            setStatus(
                "Microphone unavailable",
                state.ready
            );
            return;
        }

        if (
            state.sending ||
            state.speaking ||
            state.listening ||
            state.recognitionStarting ||
            (
                state.voiceSession &&
                state.muted
            )
        ) {
            return;
        }

        state.recognitionStarting = true;

        try {
            state.recognition.start();
        } catch (error) {
            state.recognitionStarting = false;

            console.warn(
                "Jarvis microphone could not start:",
                error
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

            const data =
                await response.json();

            if (
                data.success &&
                data.chat_ready
            ) {
                setStatus(
                    "Online",
                    true
                );
                return;
            }

            setStatus(
                "Unavailable",
                false
            );
        } catch (error) {
            console.error(
                "Jarvis status error:",
                error
            );

            setStatus(
                "Offline",
                false
            );
        }
    }

    async function sendMessage(message) {
        const cleanMessage = String(
            message || ""
        ).trim();

        if (
            !cleanMessage ||
            state.sending
        ) {
            return;
        }

        addMessage(
            "user",
            cleanMessage
        );

        setSending(true);

        try {
            const response =
                await fetch(
                    "/jarvis/chat",
                    {
                        method: "POST",
                        credentials: "same-origin",
                        headers: {
                            "Content-Type":
                                "application/json",
                        },
                        body: JSON.stringify({
                            message:
                                cleanMessage,
                        }),
                    }
                );

            if (!response.ok) {
                throw new Error(
                    `Chat request failed (${response.status}).`
                );
            }

            const data =
                await response.json();

            if (!data.success) {
                throw new Error(
                    data.error ||
                    "Jarvis could not respond."
                );
            }

            const reply =
                data.reply ||
                "I didn't receive a response.";

            addMessage(
                "assistant",
                reply
            );

            speak(reply);
        } catch (error) {
            console.error(
                "Jarvis chat error:",
                error
            );

            addMessage(
                "assistant",
                `I couldn't complete that request. ${
                    error.message ||
                    "Please try again."
                }`
            );
        } finally {
            setSending(false);

            if (
                state.voiceSession &&
                !state.muted &&
                !state.speaking &&
                !state.listening &&
                !state.recognitionStarting
            ) {
                updateVoiceMode(
                    "listening",
                    "Listening..."
                );

                window.setTimeout(
                    () => {
                        if (
                            state.voiceSession &&
                            !state.muted &&
                            !state.sending &&
                            !state.speaking &&
                            !state.listening &&
                            !state.recognitionStarting
                        ) {
                            startListening();
                        }
                    },
                    350
                );
            }

            const { input } =
                getElements();

            if (input) {
                input.focus();
            }
        }
    }

    function bindJarvis() {
        const {
            form,
            input,
            voiceButton,
            listenButton,
            stopButton,
        } = getElements();

        const orb =
            document.getElementById("jarvis-orb");

        if (orb) {
            orb.addEventListener(
                "click",
                () => {
                    if (state.speaking) {
                        stopSpeaking();
                        return;
                    }

                    const drawer =
                        document.getElementById(
                            "jarvis-drawer"
                        );
                    const backdrop =
                        document.getElementById(
                            "jarvis-drawer-backdrop"
                        );

                    if (drawer) {
                        drawer.classList.toggle("open");

                        const isOpen =
                            drawer.classList.contains("open");

                        drawer.setAttribute(
                            "aria-hidden",
                            String(!isOpen)
                        );

                        backdrop?.classList.toggle(
                            "open",
                            isOpen
                        );

                        backdrop?.setAttribute(
                            "aria-hidden",
                            String(!isOpen)
                        );

                        document.body.classList.toggle(
                            "jarvis-drawer-open",
                            isOpen
                        );
                    }

                    window.setTimeout(
                        () => {
                            const {
                                input,
                            } = getElements();

                            input?.focus();
                        },
                        100
                    );
                }
            );
        }

        const closeDrawer = () => {
            const drawer =
                document.getElementById(
                    "jarvis-drawer"
                );
            const backdrop =
                document.getElementById(
                    "jarvis-drawer-backdrop"
                );

            drawer?.classList.remove("open");
            backdrop?.classList.remove("open");

            drawer?.setAttribute(
                "aria-hidden",
                "true"
            );

            backdrop?.setAttribute(
                "aria-hidden",
                "true"
            );

            document.body.classList.remove(
                "jarvis-drawer-open"
            );
        };

        const drawerClose =
            document.getElementById(
                "jarvis-drawer-close"
            );

        const drawerBackdrop =
            document.getElementById(
                "jarvis-drawer-backdrop"
            );

        drawerClose?.addEventListener(
            "click",
            closeDrawer
        );

        drawerBackdrop?.addEventListener(
            "click",
            closeDrawer
        );

        document.addEventListener(
            "keydown",
            event => {
                if (event.key === "Escape") {
                    closeDrawer();
                }
            }
        );
        if (!form || !input) {
            return;
        }

        const SpeechRecognition =
            window.SpeechRecognition ||
            window.webkitSpeechRecognition;

        if (SpeechRecognition) {
            const recognition =
                new SpeechRecognition();

            recognition.lang = "en-US";
            recognition.continuous = false;
            recognition.interimResults = false;
            recognition.maxAlternatives = 1;

            state.recognition = recognition;

            recognition.onstart = () => {
                state.recognitionStarting = false;

                setListening(true);

                if (
                    state.voiceSession &&
                    !state.muted
                ) {
                    updateVoiceMode(
                        "listening",
                        "Listening..."
                    );
                }
            };

            recognition.onend = () => {
                state.recognitionStarting = false;

                setListening(false);
            };

            recognition.onerror = event => {
                state.recognitionStarting = false;

                setListening(false);

                console.warn(
                    "Jarvis microphone error:",
                    event.error
                );

                if (
                    event.error === "not-allowed" ||
                    event.error === "service-not-allowed"
                ) {
                    state.voiceSession = false;

                    updateVoiceMode(
                        null,
                        "Microphone permission denied"
                    );

                    setStatus(
                        "Microphone permission denied",
                        state.ready
                    );

                    return;
                }

                if (
                    state.voiceSession &&
                    !state.muted
                ) {
                    updateVoiceMode(
                        null,
                        "Microphone paused"
                    );
                }
            };

            recognition.onresult = event => {
                const transcript =
                    event.results?.[0]?.[0]
                        ?.transcript?.trim();

                if (!transcript) {
                    return;
                }

                input.value = transcript;

                if (state.voiceSession) {
                    updateVoiceMode(
                        "thinking",
                        "Thinking..."
                    );
                }

                stopListening();

                sendMessage(transcript);
            };

            if (listenButton) {
                listenButton.addEventListener(
                    "click",
                    () => {
                        if (state.voiceSession) {
                            endVoiceSession();
                            return;
                        }

                        startVoiceSession();
                    }
                );
            }

            const voiceMuteButton =
                document.getElementById(
                    "jarvis-voice-mute"
                );

            const voiceEndButton =
                document.getElementById(
                    "jarvis-voice-end"
                );

            voiceMuteButton?.addEventListener(
                "click",
                toggleVoiceMute
            );

            voiceEndButton?.addEventListener(
                "click",
                endVoiceSession
            );
        } else if (listenButton) {
            listenButton.disabled = true;
            listenButton.textContent =
                "Mic unavailable";
        }

        if (
            !("speechSynthesis" in window)
        ) {
            state.voiceEnabled = false;

            if (voiceButton) {
                voiceButton.disabled = true;
                voiceButton.textContent =
                    "Voice unavailable";
            }
        }

        updateVoiceControls();

        if (voiceButton) {
            voiceButton.addEventListener(
                "click",
                () => {
                    state.voiceEnabled =
                        !state.voiceEnabled;

                    if (
                        !state.voiceEnabled
                    ) {
                        stopSpeaking();
                    }

                    updateVoiceControls();
                }
            );
        }

        if (stopButton) {
            stopButton.addEventListener(
                "click",
                stopSpeaking
            );
        }

        form.addEventListener(
            "submit",
            async event => {
                event.preventDefault();

                const message =
                    input.value.trim();

                if (!message) {
                    return;
                }

                input.value = "";

                await sendMessage(
                    message
                );
            }
        );

        input.addEventListener(
            "keydown",
            event => {
                if (
                    event.key === "Enter" &&
                    !event.shiftKey
                ) {
                    event.preventDefault();
                    form.requestSubmit();
                }
            }
        );

        if (
            "speechSynthesis" in window
        ) {
            window.speechSynthesis
                .getVoices();
        }

        loadStatus();
    }

    document.addEventListener(
        "DOMContentLoaded",
        bindJarvis
    );

    window.refreshJarvisStatus =
        loadStatus;

    window.jarvisSpeak = speak;
    window.jarvisStopSpeaking =
        stopSpeaking;
})();