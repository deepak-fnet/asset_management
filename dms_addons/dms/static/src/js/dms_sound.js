/**
 * Small synthesized sound effects for the DMS (Web Audio, no audio files).
 * Every sound is short and quiet; the user can mute them from the sidebar.
 */

let ctx = null;
let muted = false;
try {
    muted = window.localStorage.getItem("dms.mute") === "1";
} catch {
    // storage unavailable: sounds stay on
}

function ac() {
    if (!ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
}

function tone(freq, start, dur, { type = "sine", gain = 0.08, slideTo = null } = {}) {
    const a = ac();
    const t0 = a.currentTime + start;
    const osc = a.createOscillator();
    const g = a.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (slideTo) osc.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(a.destination);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
}

function noise(start, dur, { gain = 0.06, from = 2000, to = 400, q = 1 } = {}) {
    const a = ac();
    const t0 = a.currentTime + start;
    const len = Math.max(1, Math.floor(a.sampleRate * dur));
    const buf = a.createBuffer(1, len, a.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = a.createBufferSource();
    src.buffer = buf;
    const f = a.createBiquadFilter();
    f.type = "bandpass";
    f.Q.value = q;
    f.frequency.setValueAtTime(from, t0);
    f.frequency.exponentialRampToValueAtTime(to, t0 + dur);
    const g = a.createGain();
    g.gain.setValueAtTime(gain, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(f).connect(g).connect(a.destination);
    src.start(t0);
}

const SOUNDS = {
    tap: () => tone(1400, 0, 0.04, { type: "triangle", gain: 0.03 }),
    open: () => noise(0, 0.18, { gain: 0.05, from: 600, to: 2400, q: 0.8 }),
    click: () => {
        tone(1800, 0, 0.03, { type: "square", gain: 0.03 });
        tone(900, 0.02, 0.05, { type: "triangle", gain: 0.03 });
    },
    folder: () => {
        noise(0, 0.32, { gain: 0.06, from: 300, to: 1600, q: 0.7 });
        tone(180, 0.02, 0.3, { type: "sine", gain: 0.06, slideTo: 360 });
        tone(1320, 0.18, 0.35, { gain: 0.025 });
        tone(1760, 0.24, 0.4, { gain: 0.02 });
    },
    paper: () => {
        for (let i = 0; i < 3; i++) noise(i * 0.11, 0.18, { gain: 0.045, from: 4000, to: 1500, q: 1.4 });
    },
    unroll: () => {
        // a long, soft paper slide with crinkles, then the wooden rod settles
        noise(0, 1.1, { gain: 0.05, from: 900, to: 2600, q: 0.5 });
        for (let i = 0; i < 7; i++) noise(0.08 + i * 0.14 + Math.random() * 0.05, 0.07, { gain: 0.035, from: 5000, to: 2500, q: 2 });
        tone(210, 1.12, 0.12, { type: "triangle", gain: 0.12, slideTo: 140 });
        noise(1.12, 0.05, { gain: 0.07, from: 1800, to: 600, q: 1.5 });
    },
    rollup: () => {
        noise(0, 0.5, { gain: 0.05, from: 2600, to: 800, q: 0.5 });
        tone(190, 0.48, 0.1, { type: "triangle", gain: 0.1, slideTo: 130 });
    },
    whoosh: () => noise(0, 0.5, { gain: 0.08, from: 200, to: 3500, q: 0.6 }),
    land: () => {
        tone(784, 0, 0.18, { gain: 0.05 });
        tone(1175, 0.08, 0.3, { gain: 0.04 });
    },
    back: () => noise(0, 0.15, { gain: 0.04, from: 2200, to: 600, q: 0.8 }),
    unlock: () => {
        tone(520, 0, 0.08, { type: "square", gain: 0.025 });
        tone(780, 0.07, 0.1, { type: "square", gain: 0.025 });
        noise(0.12, 0.35, { gain: 0.05, from: 300, to: 1800 });
    },
    lock: () => {
        tone(780, 0, 0.06, { type: "square", gain: 0.025 });
        tone(420, 0.06, 0.1, { type: "square", gain: 0.025 });
    },
    success: () => {
        tone(660, 0, 0.12, { gain: 0.07 });
        tone(990, 0.09, 0.22, { gain: 0.06 });
    },
    stamp: () => {
        tone(140, 0, 0.18, { type: "sine", gain: 0.25, slideTo: 60 });
        noise(0, 0.08, { gain: 0.12, from: 900, to: 200, q: 0.6 });
    },
    upload: () => {
        noise(0, 0.3, { gain: 0.05, from: 400, to: 3000 });
        tone(500, 0.05, 0.25, { gain: 0.04, slideTo: 1100 });
    },
    trash: () => {
        noise(0, 0.25, { gain: 0.08, from: 3000, to: 300, q: 2 });
        tone(300, 0.05, 0.25, { type: "triangle", gain: 0.05, slideTo: 90 });
    },
    error: () => {
        tone(220, 0, 0.12, { type: "sawtooth", gain: 0.035 });
        tone(180, 0.12, 0.16, { type: "sawtooth", gain: 0.035 });
    },
};

export const sfx = {
    play(name) {
        if (muted || !SOUNDS[name]) return;
        try {
            ac() && SOUNDS[name]();
        } catch {
            // audio is a nicety; never break the UI for it
        }
    },
    get muted() {
        return muted;
    },
    toggle() {
        muted = !muted;
        try {
            window.localStorage.setItem("dms.mute", muted ? "1" : "0");
        } catch {
            // ignore
        }
        if (!muted) this.play("tap");
        return muted;
    },
};
