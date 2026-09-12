/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { VoiceRecorder, blobToBase64 } from "./voice_recorder";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * `widget="voice_dictation"` for Char/Text fields: adds a mic button that
 * dictates straight into the field, appending each transcribed chunk.
 */
class VoiceDictationWidget extends Component {
    static template = "voice_to_text.DictationWidget";
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({
            enabled: true,
            isRecording: false,
            chunkSeconds: 4,
            error: "",
        });
        this._recorder = null;

        onWillStart(async () => {
            try {
                const config = await rpc("/api/voice_to_text/config", {});
                this.state.enabled = config.enabled;
                this.state.chunkSeconds = config.chunk_seconds || 4;
            } catch {
                this.state.enabled = false;
            }
        });

        onWillUnmount(() => this._recorder?.stop());
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
        try {
            const audioB64 = await blobToBase64(blob);
            const result = await rpc("/api/voice_to_text/transcribe", {
                audio_b64: audioB64,
                duration: durationSeconds,
            });
            if (result.ok && result.text) {
                const current = this.props.record.data[this.props.name] || "";
                const updated = current ? current + " " + result.text : result.text;
                await this.props.record.update({ [this.props.name]: updated });
            } else if (!result.ok) {
                this.state.error = result.message;
            }
        } catch (e) {
            this.state.error = e.message || String(e);
        }
    }
}

registry.category("fields").add("voice_dictation", {
    component: VoiceDictationWidget,
    supportedTypes: ["char", "text"],
});
