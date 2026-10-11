import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { playSound } from "../sounds";

const SIZE = 640;

/** Center-crop any image/video source to a square JPEG data URL. */
function squareJpeg(source, width, height, mirror) {
    const side = Math.min(width, height);
    const canvas = document.createElement("canvas");
    canvas.width = SIZE;
    canvas.height = SIZE;
    const c = canvas.getContext("2d");
    if (mirror) {
        c.translate(SIZE, 0);
        c.scale(-1, 1);
    }
    c.drawImage(source, (width - side) / 2, (height - side) / 2, side, side, 0, 0, SIZE, SIZE);
    return canvas.toDataURL("image/jpeg", 0.88);
}

/**
 * Webcam face capture: live view -> 3-2-1 countdown -> preview -> Use / Retake.
 * Browsers only expose the webcam on HTTPS/localhost; on plain HTTP we fall back to a
 * file input with capture="user", which opens the camera app on phones/tablets.
 * Resolves with a square JPEG (base64, no data-uri prefix) through props.onCapture.
 */
export class CameraDialog extends Component {
    static template = "visitor_desk.CameraDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        onCapture: Function,
        title: { type: String, optional: true },
    };

    setup() {
        this.videoRef = useRef("video");
        this.fileRef = useRef("file");
        this.insecure = !window.isSecureContext;
        this.state = useState({
            phase: "starting", // starting | live | countdown | preview | error
            count: 0,
            preview: null,
            error: "",
            flash: false,
        });
        this.stream = null;
        onMounted(() => this.start());
        onWillUnmount(() => this.stop());
    }

    async start() {
        if (!navigator.mediaDevices?.getUserMedia) {
            this.state.phase = "error";
            this.state.error = this.insecure
                ? _t("The browser blocks the webcam on plain http:// pages.")
                : _t("No camera API available in this browser.");
            return;
        }
        this.state.phase = "starting";
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
                audio: false,
            });
            const video = this.videoRef.el;
            video.srcObject = this.stream;
            await video.play();
            this.state.phase = "live";
        } catch (err) {
            this.state.phase = "error";
            this.state.error =
                err?.name === "NotAllowedError"
                    ? _t("Camera permission was denied. Allow it from the browser address bar and retry.")
                    : err?.name === "NotFoundError"
                    ? _t("No camera found on this device.")
                    : _t("Could not start the camera: %s", err?.message || err);
        }
    }

    stop() {
        this.stream?.getTracks().forEach((t) => t.stop());
        this.stream = null;
    }

    async startCountdown() {
        this.state.phase = "countdown";
        for (const n of [3, 2, 1]) {
            this.state.count = n;
            playSound("tick");
            await new Promise((r) => setTimeout(r, 650));
            if (this.state.phase !== "countdown") {
                return;
            }
        }
        this.snap();
    }

    snap() {
        const video = this.videoRef.el;
        // mirrored so the saved photo matches what the visitor saw on screen
        this.showPreview(squareJpeg(video, video.videoWidth, video.videoHeight, true));
    }

    showPreview(dataUrl) {
        playSound("shutter");
        this.state.preview = dataUrl;
        this.state.flash = true;
        setTimeout(() => (this.state.flash = false), 250);
        this.state.phase = "preview";
    }

    pickFile() {
        this.fileRef.el.click();
    }

    onFile(ev) {
        const file = ev.target.files?.[0];
        ev.target.value = "";
        if (!file) {
            return;
        }
        const img = new Image();
        img.onload = () => {
            this.showPreview(squareJpeg(img, img.naturalWidth, img.naturalHeight, false));
            URL.revokeObjectURL(img.src);
        };
        img.src = URL.createObjectURL(file);
    }

    retake() {
        this.state.preview = null;
        if (this.stream) {
            this.state.phase = "live";
        } else {
            this.start();
        }
    }

    usePhoto() {
        this.props.onCapture(this.state.preview.split(",")[1]);
        this.props.close();
    }
}
