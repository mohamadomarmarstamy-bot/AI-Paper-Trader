"use strict";

(() => {
    const state = {
        ready: false,
        sending: false,
        voiceEnabled: false,
        speaking: false,
        listening: false,
        recognition: null,
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
        };

        utterance.onend = () => {
            setSpeaking(false);
            updateVoiceControls();
        };

        utterance.onerror = () => {
            setSpeaking(false);
            updateVoiceControls();
        };

        window.speechSynthesis.speak(
            utterance
        );

        updateVoiceControls();
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

        if (state.sending) {
            return;
        }

        if (state.speaking) {
            stopSpeaking();
        }

        try {
            state.recognition.start();
        } catch (error) {
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

                    const jarvisNav =
                        document.querySelector(
                            '.nav-button[data-section="jarvis-section"]'
                        );

                    if (jarvisNav) {
                        jarvisNav.click();
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
                setListening(true);
            };

            recognition.onend = () => {
                setListening(false);
            };

            recognition.onerror = event => {
                setListening(false);

                console.warn(
                    "Jarvis microphone error:",
                    event.error
                );

                if (
                    event.error === "not-allowed" ||
                    event.error === "service-not-allowed"
                ) {
                    setStatus(
                        "Microphone permission denied",
                        state.ready
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

                stopListening();

                sendMessage(transcript);
            };

            if (listenButton) {
                listenButton.addEventListener(
                    "click",
                    () => {
                        if (state.listening) {
                            stopListening();
                            return;
                        }

                        startListening();
                    }
                );
            }
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