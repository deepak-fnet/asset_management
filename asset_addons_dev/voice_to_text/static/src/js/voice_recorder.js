/** @odoo-module **/

export const TARGET_SAMPLE_RATE = 16000;

// Minimal 16-bit PCM WAV encoder for a mono Float32Array at a given sample rate.
function encodeWav(samples, sampleRate) {
    const bytesPerSample = 2;
    const dataSize = samples.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    function writeString(offset, str) {
        for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    }

    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true); // PCM chunk size
    view.setUint16(20, 1, true); // PCM format
    view.setUint16(22, 1, true); // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * bytesPerSample, true); // byte rate
    view.setUint16(32, bytesPerSample, true); // block align
    view.setUint16(34, 16, true); // bits per sample
    writeString(36, "data");
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        offset += 2;
    }
    return new Blob([buffer], { type: "audio/wav" });
}

// Simple linear-interpolation resampler (mic input rate, typically 44.1/48kHz, → 16kHz).
function resample(float32Arr, fromRate, toRate) {
    if (fromRate === toRate) return float32Arr;
    const ratio = fromRate / toRate;
    const newLength = Math.round(float32Arr.length / ratio);
    const result = new Float32Array(newLength);
    for (let i = 0; i < newLength; i++) {
        const srcIndex = i * ratio;
        const i0 = Math.floor(srcIndex);
        const i1 = Math.min(i0 + 1, float32Arr.length - 1);
        const frac = srcIndex - i0;
        result[i] = float32Arr[i0] * (1 - frac) + float32Arr[i1] * frac;
    }
    return result;
}

/**
 * Captures microphone audio and emits fixed-length 16kHz mono WAV chunks.
 *
 * Chunk boundaries: since Parakeet TDT is not a cache-aware streaming model,
 * this delivers "pseudo-live" transcription — each fixed window is
 * transcribed independently once fully captured, not truly real-time.
 *
 * Uses ScriptProcessorNode rather than AudioWorklet: this is an internal
 * testing tool, and ScriptProcessorNode needs no separate worklet module
 * file to load, which keeps this self-contained. It is deprecated but still
 * broadly supported.
 */
export class VoiceRecorder {
    constructor({ chunkSeconds = 4, onChunk, onError } = {}) {
        this.chunkSeconds = chunkSeconds;
        this.onChunk = onChunk;
        this.onError = onError;
        this._buffer = [];
        this._bufferedSamples = 0;
        this._stream = null;
        this._audioContext = null;
        this._source = null;
        this._processor = null;
        this.isRecording = false;
    }

    async start() {
        if (this.isRecording) return;
        try {
            this._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e) {
            this.onError?.("Microphone access denied or unavailable: " + (e.message || e));
            return;
        }

        this._audioContext = new (window.AudioContext || window.webkitAudioContext)();
        this._source = this._audioContext.createMediaStreamSource(this._stream);
        this._processor = this._audioContext.createScriptProcessor(4096, 1, 1);

        const inputRate = this._audioContext.sampleRate;
        const samplesPerChunk = this.chunkSeconds * TARGET_SAMPLE_RATE;

        this._processor.onaudioprocess = (event) => {
            const input = event.inputBuffer.getChannelData(0);
            const downsampled = resample(input, inputRate, TARGET_SAMPLE_RATE);
            this._buffer.push(downsampled);
            this._bufferedSamples += downsampled.length;

            if (this._bufferedSamples >= samplesPerChunk) {
                this._flushChunk(samplesPerChunk);
            }
        };

        this._source.connect(this._processor);
        // Some browsers only fire onaudioprocess while the node is connected
        // to a destination, even though we don't want to hear the audio back.
        this._processor.connect(this._audioContext.destination);

        this.isRecording = true;
    }

    _mergeBuffer() {
        const merged = new Float32Array(this._bufferedSamples);
        let offset = 0;
        for (const part of this._buffer) {
            merged.set(part, offset);
            offset += part.length;
        }
        return merged;
    }

    _flushChunk(samplesPerChunk) {
        const merged = this._mergeBuffer();
        const chunkSamples = merged.subarray(0, samplesPerChunk);
        const remainder = merged.subarray(samplesPerChunk);

        const wavBlob = encodeWav(chunkSamples, TARGET_SAMPLE_RATE);
        const durationSeconds = chunkSamples.length / TARGET_SAMPLE_RATE;
        this.onChunk?.(wavBlob, durationSeconds);

        this._buffer = remainder.length ? [remainder] : [];
        this._bufferedSamples = remainder.length;
    }

    stop() {
        if (!this.isRecording) return;
        this.isRecording = false;

        if (this._bufferedSamples > 0) {
            const merged = this._mergeBuffer();
            const wavBlob = encodeWav(merged, TARGET_SAMPLE_RATE);
            this.onChunk?.(wavBlob, merged.length / TARGET_SAMPLE_RATE);
            this._buffer = [];
            this._bufferedSamples = 0;
        }

        this._processor?.disconnect();
        this._source?.disconnect();
        this._stream?.getTracks().forEach((t) => t.stop());
        this._audioContext?.close();

        this._processor = null;
        this._source = null;
        this._stream = null;
        this._audioContext = null;
    }
}

export function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result.split(",")[1]);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
    });
}
