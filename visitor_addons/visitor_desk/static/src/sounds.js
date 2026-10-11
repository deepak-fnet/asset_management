/**
 * Tiny synthesized sound kit (Web Audio, no files). Each cue is short and soft so it
 * can play all day at a front desk without becoming annoying.
 */
let ctx = null;

function audio() {
    if (!ctx) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) {
            return null;
        }
        ctx = new Ctx();
    }
    if (ctx.state === "suspended") {
        ctx.resume();
    }
    return ctx;
}

/** Bell-like note: sine + a quiet octave overtone with a fast attack and soft decay. */
function bell(ac, freq, start, { length = 0.6, volume = 0.18, type = "sine" } = {}) {
    const t = ac.currentTime + start;
    const out = ac.createGain();
    out.gain.setValueAtTime(0.0001, t);
    out.gain.exponentialRampToValueAtTime(volume, t + 0.015);
    out.gain.exponentialRampToValueAtTime(0.0001, t + length);
    out.connect(ac.destination);
    for (const [mult, vol] of [[1, 1], [2, 0.25], [3, 0.08]]) {
        const osc = ac.createOscillator();
        const g = ac.createGain();
        osc.type = type;
        osc.frequency.setValueAtTime(freq * mult, t);
        g.gain.value = vol;
        osc.connect(g).connect(out);
        osc.start(t);
        osc.stop(t + length + 0.05);
    }
}

function noise(ac, start, length, { volume = 0.25, freq = 2500 } = {}) {
    const t = ac.currentTime + start;
    const buffer = ac.createBuffer(1, Math.ceil(ac.sampleRate * length), ac.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) {
        data[i] = (Math.random() * 2 - 1) * (1 - i / data.length);
    }
    const src = ac.createBufferSource();
    src.buffer = buffer;
    const filter = ac.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = freq;
    const g = ac.createGain();
    g.gain.value = volume;
    src.connect(filter).connect(g).connect(ac.destination);
    src.start(t);
}

const CUES = {
    // rising major arpeggio: "welcome in"
    checkin(ac) {
        bell(ac, 659.25, 0); // E5
        bell(ac, 830.61, 0.09); // G#5
        bell(ac, 987.77, 0.18, { length: 0.9 }); // B5
    },
    // gentle falling pair: "goodbye"
    checkout(ac) {
        bell(ac, 880, 0, { volume: 0.14 }); // A5
        bell(ac, 659.25, 0.12, { volume: 0.14, length: 0.8 }); // E5
    },
    // bright chord + sparkle: new registration
    success(ac) {
        for (const f of [523.25, 659.25, 783.99]) {
            bell(ac, f, 0, { volume: 0.1, length: 0.9 });
        }
        bell(ac, 1567.98, 0.12, { volume: 0.06, length: 0.5 });
        bell(ac, 2093, 0.2, { volume: 0.05, length: 0.5 });
    },
    // two short low pulses
    error(ac) {
        bell(ac, 196, 0, { volume: 0.16, length: 0.18, type: "triangle" });
        bell(ac, 164.81, 0.2, { volume: 0.16, length: 0.3, type: "triangle" });
    },
    // camera countdown tick
    tick(ac) {
        bell(ac, 1318.5, 0, { volume: 0.08, length: 0.08 });
    },
    // camera shutter
    shutter(ac) {
        noise(ac, 0, 0.05, { volume: 0.5, freq: 4000 });
        noise(ac, 0.07, 0.09, { volume: 0.35, freq: 1800 });
    },
    // soft UI blip (returning visitor recognised)
    blip(ac) {
        bell(ac, 1046.5, 0, { volume: 0.07, length: 0.2 });
        bell(ac, 1396.9, 0.07, { volume: 0.07, length: 0.3 });
    },
};

export function playSound(name) {
    try {
        const ac = audio();
        if (ac && CUES[name]) {
            CUES[name](ac);
        }
    } catch {
        // sound is a nicety only
    }
}
