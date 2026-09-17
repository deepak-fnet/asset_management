/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { VoiceRecorder, blobToBase64 } from "./voice_recorder";

class VoiceToTextConsole extends Component {
    static template = "voice_to_text.Console";
    static props = { "*": true };

    setup() {
        this.state = useState({
            enabled: true,
            isRecording: false,
            chunkSeconds: 4,
            selectedModel: "parakeet",
            segments: [], // {text, durationSeconds, latencyMs, ok, error}
            error: "",
            pendingChunks: 0,
        });

        this._recorder = null;

        onWillStart(async () => {
            try {
                const config = await rpc("/api/voice_to_text/config", {});
                this.state.enabled = config.enabled;
                this.state.chunkSeconds = config.chunk_seconds || 4;
            } catch {
                this.state.error = "Could not load configuration.";
            }
        });

        onWillUnmount(() => this._recorder?.stop());
    }

    get fullTranscript() {
        return this.state.segments
            .filter((s) => s.ok)
            .map((s) => s.text)
            .join(" ");
    }

    get totalWords() {
        return this.fullTranscript.split(/\s+/).filter(Boolean).length;
    }

    get totalDuration() {
        return this.state.segments.reduce((sum, s) => sum + (s.durationSeconds || 0), 0);
    }

    get wordsPerMinute() {
        if (!this.totalDuration) return 0;
        return Math.round((this.totalWords / this.totalDuration) * 60);
    }

    get averageLatencyMs() {
        const ok = this.state.segments.filter((s) => s.ok);
        if (!ok.length) return 0;
        return Math.round(ok.reduce((sum, s) => sum + s.latencyMs, 0) / ok.length);
    }

    async toggleRecording() {
        if (this.state.isRecording) {
            this._recorder?.stop();
            this.state.isRecording = false;
            return;
        }

        this.state.error = "";
        this._recorder = new VoiceRecorder({
            chunkSeconds: this.state.chunkSeconds,
            onChunk: (blob, durationSeconds) => this._handleChunk(blob, durationSeconds),
            onError: (msg) => {
                this.state.error = msg;
                this.state.isRecording = false;
            },
        });
        await this._recorder.start();
        if (this._recorder.isRecording) this.state.isRecording = true;
    }

    async _handleChunk(blob, durationSeconds) {
        this.state.pendingChunks++;
        try {
            const audioB64 = await blobToBase64(blob);
            const result = await rpc("/api/voice_to_text/transcribe", {
                audio_b64: audioB64,
                duration: durationSeconds,
                model: this.state.selectedModel,
            });
            if (result.ok) {
                this.state.segments.push({
                    ok: true,
                    text: result.text,
                    durationSeconds,
                    latencyMs: Math.round(result.latency_ms || 0),
                });
            } else {
                this.state.segments.push({
                    ok: false,
                    error: result.message,
                    durationSeconds,
                });
            }
        } catch (e) {
            this.state.segments.push({
                ok: false,
                error: e.message || String(e),
                durationSeconds,
            });
        } finally {
            this.state.pendingChunks--;
        }
    }

    clearTranscript() {
        this.state.segments = [];
    }

    setModel(ev) {
        this.state.selectedModel = ev.target.value;
        // Keep comparisons clean — stats/transcript shouldn't mix across models.
        this.state.segments = [];
    }
}

registry.category("actions").add("voice_to_text_console", VoiceToTextConsole);
