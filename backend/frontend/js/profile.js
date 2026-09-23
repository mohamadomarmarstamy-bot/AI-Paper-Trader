"use strict";

(() => {
    const PROFILE_ENDPOINT = "/jarvis/profile";

    function get(id) {
        return document.getElementById(id);
    }

    function setStatus(message, success = false) {
        const element = get(
            "jarvis-profile-status"
        );

        if (!element) {
            return;
        }

        element.textContent = message;
        element.classList.toggle(
            "success",
            success
        );
    }

    function readForm() {
        return {
            preferred_name:
                get("jarvis-profile-name")?.value.trim() || "",

            address_as:
                get("jarvis-profile-address-as")?.value.trim() || "Sir",

            timezone:
                get("jarvis-profile-timezone")?.value ||
                "America/New_York",

            language:
                get("jarvis-profile-language")?.value ||
                "en-US",

            voice:
                get("jarvis-profile-voice")?.value ||
                "cedar",

            wake_phrase:
                get("jarvis-profile-wake-phrase")?.value.trim() ||
                "Hey Jarvis",

            voice_activation:
                Boolean(
                    get(
                        "jarvis-profile-voice-activation"
                    )?.checked
                ),

            automatic_greeting:
                Boolean(
                    get(
                        "jarvis-profile-automatic-greeting"
                    )?.checked
                ),
        };
    }

    function fillForm(profile) {
        if (!profile) {
            return;
        }

        const fields = {
            "jarvis-profile-name":
                profile.preferred_name || "",

            "jarvis-profile-address-as":
                profile.address_as || "Sir",

            "jarvis-profile-timezone":
                profile.timezone || "America/New_York",

            "jarvis-profile-language":
                profile.language || "en-US",

            "jarvis-profile-voice":
                profile.voice || "cedar",

            "jarvis-profile-wake-phrase":
                profile.wake_phrase || "Hey Jarvis",
        };

        Object.entries(fields).forEach(
            ([id, value]) => {
                const element = get(id);

                if (element) {
                    element.value = value;
                }
            }
        );

        const voiceActivation =
            get(
                "jarvis-profile-voice-activation"
            );

        if (voiceActivation) {
            voiceActivation.checked =
                Boolean(
                    profile.voice_activation
                );
        }

        const automaticGreeting =
            get(
                "jarvis-profile-automatic-greeting"
            );

        if (automaticGreeting) {
            automaticGreeting.checked =
                profile.automatic_greeting !== false;
        }
    }

    async function loadProfile() {
        try {
            const response = await fetch(
                PROFILE_ENDPOINT,
                {
                    method: "GET",
                    credentials: "same-origin",
                    headers: {
                        "Accept": "application/json",
                    },
                }
            );

            if (!response.ok) {
                throw new Error(
                    `Profile request failed (${response.status}).`
                );
            }

            const data =
                await response.json();

            if (!data.success) {
                throw new Error(
                    data.error ||
                    "Unable to load Jarvis profile."
                );
            }

            fillForm(data.profile);
            setStatus("Profile loaded.", true);

            window.jarvisProfile =
                data.profile;

            return data.profile;
        } catch (error) {
            console.error(
                "Jarvis profile load error:",
                error
            );

            setStatus(
                error.message ||
                "Unable to load profile."
            );

            return null;
        }
    }

    async function saveProfile() {
        const button = get(
            "jarvis-profile-save"
        );

        const profile =
            readForm();

        if (button) {
            button.disabled = true;
        }

        setStatus("Saving...");

        try {
            const response = await fetch(
                PROFILE_ENDPOINT,
                {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type":
                            "application/json",
                        "Accept":
                            "application/json",
                    },
                    body: JSON.stringify(
                        profile
                    ),
                }
            );

            if (!response.ok) {
                throw new Error(
                    `Profile save failed (${response.status}).`
                );
            }

            const data =
                await response.json();

            if (!data.success) {
                throw new Error(
                    data.error ||
                    "Unable to save Jarvis profile."
                );
            }

            fillForm(data.profile);

            window.jarvisProfile =
                data.profile;

            setStatus(
                "Profile saved.",
                true
            );

            if (
                typeof window.onJarvisProfileUpdated ===
                "function"
            ) {
                window.onJarvisProfileUpdated(
                    data.profile
                );
            }

            return data.profile;
        } catch (error) {
            console.error(
                "Jarvis profile save error:",
                error
            );

            setStatus(
                error.message ||
                "Unable to save profile."
            );

            return null;
        } finally {
            if (button) {
                button.disabled = false;
            }
        }
    }

    function bind() {
        const saveButton =
            get("jarvis-profile-save");

        if (saveButton) {
            saveButton.addEventListener(
                "click",
                saveProfile
            );
        }

        loadProfile();
    }

    window.loadJarvisProfile =
        loadProfile;

    window.saveJarvisProfile =
        saveProfile;

    document.addEventListener(
        "DOMContentLoaded",
        bind
    );
})();
