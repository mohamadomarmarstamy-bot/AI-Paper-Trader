"use strict";

(() => {
    const state = {
        speechVersion: 0,
        greeted: false,
        awaitingWakeCommand: false,
        pendingMessage: null,
        ready: false,
        sending: false,
        voiceEnabled: false,
        speaking: false,
        speechPending: false,
        currentAudio: null,
        currentAudioUrl: null,
        speechController: null,
        listening: false,
        recognition: null,
        voiceSession: false,
        muted: false,
        recognitionStarting: false,
        conversationHistory: [],
        bargeInStream: null,
        bargeInContext: null,
        bargeInAnalyser: null,
        bargeInFrame: null,
        bargeInStartedAt: 0,
        bargeInLoudSince: 0,
        bargeInBaseline: 0,
        bargeInTriggered: false,
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
            stopButton.disabled = !state.speaking && !state.speechPending;
        }
    }

    function stopSpeaking() {
        state.speechVersion++;
        stopBargeInDetection();

        if (state.speechController) {
            try {
                state.speechController.abort();
            } catch (error) {
                console.warn(
                    "Jarvis speech request could not abort:",
                    error
                );
            }

            state.speechController = null;
        }

        if (state.currentAudio) {
            try {
                state.currentAudio.onplay = null;
                state.currentAudio.onended = null;
                state.currentAudio.onerror = null;
                state.currentAudio.pause();
                state.currentAudio.currentTime = 0;
            } catch (error) {
                console.warn(
                    "Jarvis audio could not stop:",
                    error
                );
            }

            state.currentAudio = null;
        }

        if (state.currentAudioUrl) {
            URL.revokeObjectURL(
                state.currentAudioUrl
            );

            state.currentAudioUrl = null;
        }

        if (
            "speechSynthesis" in window
        ) {
            window.speechSynthesis.cancel();
        }

        state.speechPending = false;

        setSpeaking(false);
        updateVoiceControls();
    }

    async function speak(text) {
        if (!state.voiceEnabled) {
            return;
        }

        const cleanText = String(
            text || ""
        )
            .replace(
                /```[\s\S]*?```/g,
                " code block omitted. "
            )
            .replace(/`([^`]+)`/g, "$1")
            .replace(/[*_#>-]/g, " ")
            .replace(
                /[\p{Extended_Pictographic}\uFE0F]/gu,
                ""
            )
            .replace(/\s+/g, " ")
            .trim();

        const speechInput=cleanText.length>3900?cleanText.slice(0,3900)+". The full answer is in the chat.":cleanText;
        if (!speechInput) {
            return;
        }

        stopSpeaking();
        stopListening();
        const speechVersion = state.speechVersion;
        let speechController = null;

        const resumeListening = () => {
            if (speechVersion !== state.speechVersion) return;
            stopBargeInDetection();
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
                            !state.speaking &&
                            !state.speechPending &&
                            !state.listening &&
                            !state.recognitionStarting
                        ) {
                            startListening();
                        }
                    },
                    300
                );
            }
        };

        const beginSpeaking = () => {
            setSpeaking(true);
            updateVoiceControls();

            if (state.voiceSession) {
                updateVoiceMode(
                    "speaking",
                    "Speaking..."
                );

                startBargeInDetection();
            }
        };

        try {
            state.speechPending = true;
            updateVoiceControls();

            speechController = new AbortController();

            state.speechController =
                speechController;

            if (state.voiceSession) {
                updateVoiceMode(
                    "thinking",
                    "Preparing voice..."
                );
            }

            const response = await fetch(
                "/jarvis/speech",
                {
                    method: "POST",
                    credentials: "same-origin",
                    signal:
                        speechController.signal,
                    headers: {
                        "Content-Type":
                            "application/json",
                    },
                    body: JSON.stringify({
                        text: speechInput,
                    }),
                }
            );

            if (!response.ok) {
                throw new Error(
                    `Neural speech failed (${response.status}).`
                );
            }

            const audioBlob =
                await response.blob();

            if (
                state.speechController ===
                speechController
            ) {
                state.speechController = null;
            }

            state.speechPending = false;

            if (speechVersion !== state.speechVersion || speechController.signal.aborted) return;
            const audioUrl =
                URL.createObjectURL(
                    audioBlob
                );

            state.currentAudioUrl =
                audioUrl;

            const audio =
                new Audio(audioUrl);

            state.currentAudio = audio;

            audio.onplay = () => {
                beginSpeaking();
            };

            const finishAudio = () => {
                if (
                    state.currentAudioUrl ===
                    audioUrl
                ) {
                    URL.revokeObjectURL(
                        audioUrl
                    );

                    state.currentAudioUrl =
                        null;
                }

                if (
                    state.currentAudio ===
                    audio
                ) {
                    state.currentAudio =
                        null;
                }

                resumeListening();
            };

            audio.onended = finishAudio;
            audio.onerror = finishAudio;

            await audio.play();
            return;
        } catch (error) {
            if (speechVersion !== state.speechVersion) return;
            if (
                state.speechController ===
                speechController
            ) {
                state.speechController = null;
            }

            state.speechPending = false;

            if (
                error?.name ===
                "AbortError"
            ) {
                return;
            }

            console.warn(
                "Jarvis neural voice failed; using browser fallback:",
                error
            );
        }

        if (
            !("speechSynthesis" in window)
        ) {
            resumeListening();
            return;
        }

        const utterance =
            new SpeechSynthesisUtterance(
                speechInput
            );

        utterance.rate = 1.02;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;

        const voices =
            window.speechSynthesis
                .getVoices();

        const preferredVoice =
            voices.find(
                voice =>
                    /en-GB/i.test(
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

        utterance.onstart =
            beginSpeaking;

        utterance.onend =
            resumeListening;

        utterance.onerror =
            resumeListening;

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
        state.awaitingWakeCommand=false;
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
        state.awaitingWakeCommand=false;
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

    function stopBargeInDetection() {
        if (state.bargeInFrame) {
            window.cancelAnimationFrame(
                state.bargeInFrame
            );

            state.bargeInFrame = null;
        }

        if (state.bargeInStream) {
            for (
                const track of
                state.bargeInStream.getTracks()
            ) {
                track.stop();
            }

            state.bargeInStream = null;
        }

        if (state.bargeInContext) {
            const context =
                state.bargeInContext;

            state.bargeInContext = null;

            if (
                context.state !== "closed"
            ) {
                context.close().catch(
                    () => {}
                );
            }
        }

        state.bargeInAnalyser = null;
        state.bargeInStartedAt = 0;
        state.bargeInLoudSince = 0;
        state.bargeInBaseline = 0;
        state.bargeInTriggered = false;
    }

    async function startBargeInDetection() {
        if (
            !state.voiceSession ||
            state.muted ||
            state.bargeInStream ||
            state.bargeInTriggered
        ) {
            return;
        }

        if (
            !navigator.mediaDevices ||
            !navigator.mediaDevices.getUserMedia
        ) {
            return;
        }

        try {
            const stream =
                await navigator.mediaDevices
                    .getUserMedia({
                        audio: {
                            echoCancellation: true,
                            noiseSuppression: true,
                            autoGainControl: true,
                        },
                    });

            if (
                !state.voiceSession ||
                state.muted ||
                (
                    !state.speaking &&
                    !state.speechPending
                )
            ) {
                for (
                    const track of
                    stream.getTracks()
                ) {
                    track.stop();
                }

                return;
            }

            const AudioContextClass =
                window.AudioContext ||
                window.webkitAudioContext;

            if (!AudioContextClass) {
                for (
                    const track of
                    stream.getTracks()
                ) {
                    track.stop();
                }

                return;
            }

            const context =
                new AudioContextClass();

            const source =
                context.createMediaStreamSource(
                    stream
                );

            const analyser =
                context.createAnalyser();

            analyser.fftSize = 1024;
            analyser.smoothingTimeConstant =
                0.35;

            source.connect(analyser);

            state.bargeInStream = stream;
            state.bargeInContext = context;
            state.bargeInAnalyser = analyser;
            state.bargeInStartedAt =
                performance.now();
            state.bargeInLoudSince = 0;
            state.bargeInBaseline = 0;
            state.bargeInTriggered = false;

            const samples =
                new Float32Array(
                    analyser.fftSize
                );

            const detect = now => {
                if (
                    !state.bargeInAnalyser ||
                    !state.voiceSession ||
                    state.muted ||
                    (
                        !state.speaking &&
                        !state.speechPending
                    )
                ) {
                    stopBargeInDetection();
                    return;
                }

                analyser.getFloatTimeDomainData(
                    samples
                );

                let sum = 0;

                for (
                    let index = 0;
                    index < samples.length;
                    index += 1
                ) {
                    const value =
                        samples[index];

                    sum += value * value;
                }

                const rms = Math.sqrt(
                    sum / samples.length
                );

                const age =
                    now -
                    state.bargeInStartedAt;

                if (age < 450) {
                    state.bargeInBaseline =
                        Math.max(
                            state.bargeInBaseline,
                            rms
                        );
                }

                const threshold =
                    Math.max(
                        0.035,
                        state.bargeInBaseline *
                            2.6
                    );

                if (
                    age >= 450 &&
                    rms >= threshold
                ) {
                    if (
                        !state.bargeInLoudSince
                    ) {
                        state.bargeInLoudSince =
                            now;
                    }

                    if (
                        now -
                            state.bargeInLoudSince >=
                        180
                    ) {
                        state.bargeInTriggered =
                            true;

                        stopSpeaking();

                        window.setTimeout(
                            () => {
                                if (
                                    state.voiceSession &&
                                    !state.muted
                                ) {
                                    startListening();
                                }
                            },
                            120
                        );

                        return;
                    }
                } else {
                    state.bargeInLoudSince = 0;
                }

                state.bargeInFrame =
                    window.requestAnimationFrame(
                        detect
                    );
            };

            state.bargeInFrame =
                window.requestAnimationFrame(
                    detect
                );
        } catch (error) {
            stopBargeInDetection();

            console.warn(
                "Jarvis barge-in detection unavailable:",
                error
            );
        }
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
            state.speechPending ||
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
                (data.chat_ready || data.settings_ready)
            ) {
                setStatus(
                    data.chat_ready ? "Online · app tools connected" : "Settings ready · AI unavailable",
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

        if (state.pendingMessage?.message !== cleanMessage) state.pendingMessage = {message:cleanMessage,id:crypto.randomUUID()};
        addMessage("user", cleanMessage);

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
                            message: cleanMessage,
                            request_id: state.pendingMessage.id,
                            ui_context: {watchlist:Array.isArray(window.watchlist)?window.watchlist.slice(0,100):[]},
                            history:
                                state.conversationHistory.slice(
                                    -12
                                ),
                        }),
                    }
                );

            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Chat request failed (${response.status}).`);

            if (!data.success) {
                state.pendingMessage = null;
                throw new Error(
                    data.error ||
                    "Jarvis could not respond."
                );
            }

            state.pendingMessage = null;
            renderActions(data);
            const reply =
                data.reply ||
                "I didn't receive a response.";

            addMessage(
                "assistant",
                reply
            );

            state.conversationHistory.push(
                {
                    role: "user",
                    content: cleanMessage,
                },
                {
                    role: "assistant",
                    content: reply,
                }
            );

            if (
                state.conversationHistory.length > 12
            ) {
                state.conversationHistory =
                    state.conversationHistory.slice(
                        -12
                    );
            }

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
                !state.speechPending &&
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
                            !state.speechPending &&
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

    function renderActions(data){
        const box=document.getElementById("jarvis-actions");if(!box)return;box.replaceChildren();
        const note=document.createElement("p");note.textContent=data.receipt?"Saved change: "+Object.entries(data.receipt.after).map(([k,v])=>k.replaceAll("_"," ")+": "+data.receipt.before[k]+" → "+v).join("; "):"No settings were changed by this reply.";box.appendChild(note);
        if(data.receipt)document.dispatchEvent(new CustomEvent("app:settings-changed",{detail:{source:"jarvis"}}));
        for(const proposal of data.proposals||[]){const card=document.createElement("article"),text=document.createElement("p"),button=document.createElement("button"),cancel=document.createElement("button");
            text.textContent="Preview (not applied): "+Object.entries(proposal.after).map(([k,v])=>k.replaceAll("_"," ")+": "+proposal.before[k]+" → "+v).join("; ")+". "+proposal.effect;
            button.type="button";button.textContent="Apply settings preview";cancel.type="button";cancel.textContent="Dismiss preview";let requestId=crypto.randomUUID();
            button.addEventListener("click",async()=>{button.disabled=true;try{const response=await fetch("/jarvis/proposals/"+encodeURIComponent(proposal.id)+"/apply",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({request_id:requestId})});const result=await response.json();if(!response.ok||!result.success)throw Error(result.detail||result.error||"Unable to confirm change; refresh settings.");text.textContent="Saved: "+Object.entries(result.receipt.after).map(([k,v])=>k.replaceAll("_"," ")+": "+result.receipt.before[k]+" → "+v).join("; ")+". "+result.effect;cancel.disabled=true;document.dispatchEvent(new CustomEvent("app:settings-changed",{detail:{source:"jarvis"}}));}catch(error){text.textContent=error.message;button.disabled=false;}});
            cancel.addEventListener("click",()=>card.remove());card.append(text,button,cancel);box.appendChild(card);
        }
    }
    function greet(){if(!window.jarvisProfile||state.greeted||window.jarvisProfile.automatic_greeting===false)return;state.greeted=true;const profile=window.jarvisProfile||{},name=profile.preferred_name||profile.address_as||"Sir";const text="Welcome, "+name+". I can inspect your app and help with its settings.";addMessage("assistant",text);if(state.voiceEnabled)speak(text);}
    function openDrawer(){const drawer=document.getElementById("jarvis-drawer"),backdrop=document.getElementById("jarvis-drawer-backdrop"),section=document.getElementById("jarvis-section");if(section){section.hidden=false;section.classList.remove("hidden");}drawer?.classList.add("open");drawer?.setAttribute("aria-hidden","false");backdrop?.classList.add("open");backdrop?.setAttribute("aria-hidden","false");document.body.classList.add("jarvis-drawer-open");greet();getElements().input?.focus();}
    function closeDrawer(){document.getElementById("jarvis-drawer")?.classList.remove("open");document.getElementById("jarvis-drawer")?.setAttribute("aria-hidden","true");document.getElementById("jarvis-drawer-backdrop")?.classList.remove("open");document.getElementById("jarvis-drawer-backdrop")?.setAttribute("aria-hidden","true");document.body.classList.remove("jarvis-drawer-open");if(state.voiceSession)endVoiceSession();else{stopSpeaking();stopListening();}}
    window.openJarvis=openDrawer;window.closeJarvis=closeDrawer;
    window.jarvisPreviewVoice=()=>{state.voiceEnabled=true;updateVoiceControls();speak("Good evening. Your assistant is ready to inspect the app and help with its settings.");};
    document.addEventListener("jarvis:profile",event=>{if(state.recognition)state.recognition.lang=event.detail?.language||"en-GB";state.awaitingWakeCommand=false;if(document.getElementById("jarvis-drawer")?.classList.contains("open"))greet();});

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

        orb?.addEventListener("click",()=>{if(state.speaking){stopSpeaking();return;}const drawer=document.getElementById("jarvis-drawer");if(drawer?.classList.contains("open"))closeDrawer();else openDrawer();});
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

            recognition.lang = window.jarvisProfile?.language || "en-GB";
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
                if (state.voiceSession && !state.muted && !state.sending && !state.speaking && !state.speechPending) window.setTimeout(startListening,300);
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

                if (!["no-speech","aborted"].includes(event.error)) state.voiceSession=false;
                if (state.voiceSession && !state.muted) updateVoiceMode(null,"Microphone paused");
            };

            recognition.onresult = event => {
                let transcript = event.results?.[0]?.[0]?.transcript?.trim();

                if (!transcript) {
                    return;
                }

                const profile=window.jarvisProfile||{};
                if(profile.voice_activation && !state.awaitingWakeCommand){
                    const wake=String(profile.wake_phrase||"Hey Jarvis").trim().toLowerCase();
                    if(!transcript.toLowerCase().startsWith(wake)||(transcript.length>wake.length&&!/^[\s,.!?]/.test(transcript.slice(wake.length)))){updateVoiceMode("listening","Say “"+(profile.wake_phrase||"Hey Jarvis")+"” first");return;}
                    transcript=transcript.slice(wake.length).replace(/^[ ,.!?]+/,"").trim();
                    if(!transcript){state.awaitingWakeCommand=true;updateVoiceMode("listening","Ready for your request…");return;}
                }
                state.awaitingWakeCommand=false;
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
            typeof Audio !== "function"
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