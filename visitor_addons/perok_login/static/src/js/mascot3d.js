/* ============================================================
   PEROK LOGIN — 3D character mascot (ES module, loaded on demand
   by login.js when the default animation is a "3D model (GLB)").

   Works with a Mixamo-rigged GLB that has an "idle" clip and,
   optionally, "run" / "walk" (used to leave on success). Everything else is procedural on
   top of the clips: the head follows the cursor, two-bone IK puts
   the hands over the eyes / up in a cheer / up for a wave.

     idle     : breathing idle with an upright, confident posture;
                waves "hi" on arrival
     email    : looks at the field, nods while typing
     shy      : both hands over the eyes (password hidden)
     peek     : one hand drops, peeking at the password
     sad      : head down, shoulders slump, shakes the head (wrong login)
     success  : cheers with a jump, then runs off over the form
     click    : waves or jumps
   ============================================================ */
import * as THREE from "../lib/three/three.module.min.js";
import { GLTFLoader } from "../lib/three/GLTFLoader.js";

const V3 = THREE.Vector3;
const Q = THREE.Quaternion;

function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
function smooth01(v) { v = clamp(v, 0, 1); return v * v * (3 - 2 * v); }
function damp(cur, target, rate, dt) { return cur + (target - cur) * (1 - Math.exp(-rate * dt)); }

function bone(root, name) {
    return root.getObjectByName("mixamorig" + name) || root.getObjectByName("mixamorig:" + name) ||
        root.getObjectByName(name);
}

/* rotate `b` (in world space) so that the direction b -> fromWorld points at
   b -> toWorld, blended by w */
const _a = new V3(), _b = new V3(), _p = new V3(), _q = new Q(), _qw = new Q(), _qp = new Q(), _q0 = new Q();
function aim(b, fromWorld, toWorld, w) {
    if (w <= 0.001) {
        return;
    }
    b.getWorldPosition(_p);
    _a.subVectors(fromWorld, _p).normalize();
    _b.subVectors(toWorld, _p).normalize();
    _q.setFromUnitVectors(_a, _b);
    b.getWorldQuaternion(_qw);
    b.parent.getWorldQuaternion(_qp).invert();
    _q0.copy(b.quaternion);
    b.quaternion.copy(_qp.multiply(_q.multiply(_qw)));
    if (w < 1) {
        b.quaternion.copy(_q0.slerp(b.quaternion, w));
    }
    b.updateMatrixWorld(true);
}

/* rotate `b` by a world-space rotation q, blended by w */
function turn(b, q, w) {
    if (w <= 0.001) {
        return;
    }
    b.getWorldQuaternion(_qw);
    b.parent.getWorldQuaternion(_qp).invert();
    _q0.copy(b.quaternion);
    _q.copy(q).slerp(new Q(), 1 - w);
    b.quaternion.copy(_qp.multiply(_q.multiply(_qw)));
    b.updateMatrixWorld(true);
}

/* two-bone IK: upper arm, forearm, hand -> target, elbow toward pole */
const _s = new V3(), _e = new V3(), _h = new V3(), _d = new V3(), _side = new V3(), _el = new V3();
function reach(arm, w) {
    if (w <= 0.001) {
        return;
    }
    arm.upper.getWorldPosition(_s);
    arm.fore.getWorldPosition(_e);
    arm.hand.getWorldPosition(_h);
    const la = _s.distanceTo(_e), lb = _e.distanceTo(_h);
    const t = arm.target;
    let dist = clamp(_s.distanceTo(t), 0.05, (la + lb) * 0.999);
    _d.subVectors(t, _s).normalize();
    _side.subVectors(arm.pole, _s);
    _side.addScaledVector(_d, -_side.dot(_d)).normalize();
    const cosA = clamp((la * la + dist * dist - lb * lb) / (2 * la * dist), -1, 1);
    const sinA = Math.sqrt(1 - cosA * cosA);
    _el.copy(_s).addScaledVector(_d, cosA * la).addScaledVector(_side, sinA * la);
    aim(arm.upper, _e, _el, w);
    arm.fore.getWorldPosition(_e);
    arm.hand.getWorldPosition(_h);
    aim(arm.fore, _h, t, w);
}

/* point the hand's fingers (hand -> index root) along dir, blended by w */
const _hw = new V3(), _fw = new V3(), _tg = new V3();
function pointHand(hand, finger, dir, w) {
    if (!finger || w <= 0.001) {
        return;
    }
    hand.getWorldPosition(_hw);
    finger.getWorldPosition(_fw);
    _tg.copy(_hw).addScaledVector(dir, _hw.distanceTo(_fw));
    aim(hand, _fw, _tg, w);
}

function blobShadow() {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d");
    const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    gr.addColorStop(0, "rgba(60,25,0,0.55)");
    gr.addColorStop(0.55, "rgba(60,25,0,0.22)");
    gr.addColorStop(1, "rgba(60,25,0,0)");
    g.fillStyle = gr;
    g.fillRect(0, 0, 128, 128);
    const tex = new THREE.CanvasTexture(c);
    const m = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
        new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
    m.rotation.x = -Math.PI / 2;
    return m;
}

export async function createMascot3D(stage, url) {
    const gltf = await new GLTFLoader().loadAsync(url);
    const model = gltf.scene;
    model.traverse((o) => {
        if (o.isMesh) {
            o.frustumCulled = false;
            if (o.material && o.material.map) {
                o.material.map.colorSpace = THREE.SRGBColorSpace;
                o.material.map.anisotropy = 4;
            }
        }
        if (/icosphere/i.test(o.name)) {
            o.visible = false;
        }
    });

    const B = {
        hips: bone(model, "Hips"), spine: bone(model, "Spine1"), chest: bone(model, "Spine2"),
        neck: bone(model, "Neck"), head: bone(model, "Head"),
        lu: bone(model, "LeftArm"), lf: bone(model, "LeftForeArm"), lh: bone(model, "LeftHand"),
        ru: bone(model, "RightArm"), rf: bone(model, "RightForeArm"), rh: bone(model, "RightHand"),
        ls: bone(model, "LeftShoulder"), rs: bone(model, "RightShoulder"),
        li: bone(model, "LeftHandIndex1"), ri: bone(model, "RightHandIndex1"),
    };
    if (!B.head || !B.lu || !B.ru || !B.lf || !B.rf || !B.lh || !B.rh) {
        throw new Error("not a Mixamo rig");
    }

    const holder = new THREE.Group();     // walking / jumping moves this
    holder.add(model);

    // clips: idle, walk (made in place), sit
    const clips = {};
    gltf.animations.forEach((c) => { clips[c.name.toLowerCase()] = c; });
    const idleClip = clips.idle || gltf.animations[0];
    [clips.walk, clips.run].filter(Boolean).forEach((clip) => {
        clip.tracks.forEach((tr) => {
            if (/hips\.position$/i.test(tr.name)) {
                const v = tr.values;
                const x0 = v[0], z0 = v[2];
                for (let i = 0; i < v.length; i += 3) {
                    v[i] = x0;
                    v[i + 2] = z0;
                }
            }
        });
    });
    const mixer = new THREE.AnimationMixer(model);
    const act = {};
    act.idle = mixer.clipAction(idleClip);
    act.idle.play();
    if (clips.walk) {
        act.walk = mixer.clipAction(clips.walk);
    }
    const leaveClip = clips.run || clips.walk;
    if (leaveClip) {
        act.leave = mixer.clipAction(leaveClip);
    }

    // size: normalise to 1.8 m, feet on y=0 (measured on the skeleton: the
    // skinned mesh's own bounds ignore the armature's scale; measured
    // on the first animation frame, which may move the hips)
    mixer.update(0);
    model.updateMatrixWorld(true);
    const top = bone(model, "HeadTop_End") || B.head;
    const toes = [bone(model, "LeftToe_End"), bone(model, "RightToe_End"), bone(model, "LeftFoot"), bone(model, "RightFoot")]
        .filter(Boolean);
    const pTop = top.getWorldPosition(new V3());
    const footY = Math.min.apply(null, toes.map((t) => t.getWorldPosition(new V3()).y));
    const k = 1.8 / Math.max(1e-4, pTop.y - footY);
    model.scale.multiplyScalar(k);
    model.position.y = -footY * k;

    // scene
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "low-power" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    const canvas = renderer.domElement;
    canvas.className = "pk-3d";
    stage.appendChild(canvas);

    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xfff1dc, 0x7a3e0a, 1.35));
    const key = new THREE.DirectionalLight(0xfff4e6, 2.3);
    key.position.set(-1.5, 3, 3);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xffc27a, 1.6);
    rim.position.set(2.5, 2.5, -2.5);
    scene.add(rim);
    const fill = new THREE.DirectionalLight(0xffe2b8, 0.6);
    fill.position.set(2, 1, 3);
    scene.add(fill);
    const shadow = blobShadow();
    shadow.scale.set(0.95, 0.6, 1);
    shadow.position.y = 0.002;
    scene.add(shadow);
    scene.add(holder);

    const camera = new THREE.PerspectiveCamera(20, 1, 0.1, 50);
    const FACING = 0.32;                  // turned a little toward the form
    holder.rotation.y = FACING;
    let baseX = 0;

    let escaped = null;     // fixed rect once the character leaves its panel
    function resize() {
        const r = escaped || stage.getBoundingClientRect();
        const w = Math.max(1, r.width), h = Math.max(1, r.height);
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        // frame 2.05 m of height, feet near the bottom
        const dist = (2.05 / 2) / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
        camera.position.set(0, 0.98, dist);
        camera.lookAt(0, 0.98, 0);
        camera.updateProjectionMatrix();
        // stand where the parrot stands: on the bird box's centre line
        const bird = stage.querySelector(".pk-stage__bird");
        const br = escaped ? escaped.bird : (bird ? bird.getBoundingClientRect() : r);
        const fx = ((br.left + br.width / 2) - r.left) / w;      // 0..1 across the stage
        const halfW = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * dist * camera.aspect;
        baseX = (fx - 0.5) * 2 * halfW;
    }
    resize();
    const ro = window.ResizeObserver ? new ResizeObserver(resize) : null;
    if (ro) {
        ro.observe(stage);
    } else {
        window.addEventListener("resize", resize);
    }

    /* ---------- behaviour state ---------- */
    let pose = "idle";
    let lookX = 0, lookY = 0, wander = 0;
    const w = { cover: 0, peek: 0, cheer: 0, wave: 0, sad: 0, look: 1, run: 0 };
    const tw = { cover: 0, peek: 0 };
    let waveUntil = 0, sadStart = -1, cheerStart = -1, walkStart = -1, nod = 0;
    const sitting = false;
    let lastActivity = performance.now();
    const t0 = performance.now();
    waveUntil = t0 + 900 + 1800;          // "hi!" shortly after arrival
    let waveFrom = t0 + 900;

    const L = { upper: B.lu, fore: B.lf, hand: B.lh, target: new V3(), pole: new V3() };
    const R = { upper: B.ru, fore: B.rf, hand: B.rh, target: new V3(), pole: new V3() };

    function standUp() {
        lastActivity = performance.now();
    }

    function poke() {
        standUp();
        const now = performance.now();
        if (Math.random() < 0.55) {
            waveFrom = now;
            waveUntil = now + 1600;
        } else {
            cheerStart = now;
            setTimeout(() => { if (pose !== "success") { cheerStart = -1; } }, 900);
        }
    }
    canvas.addEventListener("click", poke);
    const login = document.getElementById("login");
    const onInput = () => { nod = 1; standUp(); };
    if (login) {
        login.addEventListener("input", onInput);
    }

    /* ---------- per frame ---------- */
    const up = new V3(0, 1, 0), fwd = new V3(), side = new V3(), hp = new V3(), tmp = new V3();
    const qa = new Q(), qb = new Q();
    let raf = 0, last = performance.now();

    function frame(now) {
        raf = requestAnimationFrame(frame);
        if (document.hidden) {
            last = now;
            return;
        }
        const dt = Math.min(0.05, (now - last) / 1000);
        last = now;

        mixer.update(dt);
        holder.position.x = baseX;
        holder.updateMatrixWorld(true);

        // targets
        const tc = pose === "shy" || pose === "peek" ? 1 : 0;
        const tp = pose === "peek" ? 1 : 0;
        const sadT = sadStart >= 0 ? (now - sadStart) / 1000 : -1;
        if (sadT > 2.4) {
            sadStart = -1;
        }
        w.cover = damp(w.cover, sitting ? 0 : tc, 9, dt);
        w.peek = damp(w.peek, tp, 7, dt);
        w.sad = damp(w.sad, sadT >= 0 && sadT < 2.0 ? 1 : 0, 6, dt);
        const waving = now > waveFrom && now < waveUntil && !sitting && tc === 0;
        w.wave = damp(w.wave, waving ? 1 : 0, 6, dt);
        w.cheer = damp(w.cheer, cheerStart >= 0 ? 1 : 0, 10, dt);
        nod = damp(nod, 0, 5, dt);

        // body axes
        holder.getWorldQuaternion(qa);
        fwd.set(0, 0, 1).applyQuaternion(qa);
        side.set(1, 0, 0).applyQuaternion(qa);        // character's left
        B.head.getWorldPosition(hp);

        // spine: slump when sad, lean back a little in a cheer
        // the idle clip slouches: straighten the back and lift the chest
        if (B.spine) {
            qb.setFromAxisAngle(side, -0.1 * (1 - w.sad) * (1 - w.run));
            turn(B.spine, qb, 1);
        }
        if (B.chest) {
            qb.setFromAxisAngle(side, 0.22 * w.sad - 0.08 * w.cheer - 0.06 * (1 - w.sad) * (1 - w.run));
            turn(B.chest, qb, 1);
        }
        // shoulders back a little
        if (B.ls && B.rs) {
            qb.setFromAxisAngle(up, -0.08 * (1 - w.sad));
            turn(B.ls, qb, 1);
            qb.setFromAxisAngle(up, 0.08 * (1 - w.sad));
            turn(B.rs, qb, 1);
        }

        // head: follow the cursor / form, droop and shake when sad, nod while typing
        const shake = sadT >= 0 ? Math.sin(sadT * Math.PI * 2 * 2.2) * Math.exp(-sadT * 1.2) * 0.35 : 0;
        const calm = (1 - w.cover * 0.6) * (1 - w.sad) * (1 - w.run);
        const yaw = clamp(lookX * 0.4 + wander * 0.25, -0.45, 0.45) * calm + shake - (pose === "email" ? 0.12 : 0) * (1 - w.run);
        // chin up by default (the idle clip looks at the floor)
        const pitch = clamp(lookY * 0.25, -0.2, 0.25) * calm - 0.16 * calm + 0.42 * w.sad + 0.18 * w.cover +
            0.12 * Math.sin(nod * Math.PI) * nod;
        qa.setFromAxisAngle(up, yaw * 0.4);
        qb.setFromAxisAngle(side, pitch * 0.4);
        qa.multiply(qb);
        if (B.neck) {
            turn(B.neck, qa, 1);
        }
        qa.setFromAxisAngle(up, yaw * 0.6);
        qb.setFromAxisAngle(side, pitch * 0.6);
        qa.multiply(qb);
        turn(B.head, qa, 1);
        B.head.getWorldPosition(hp);

        // arms
        // wrist targets: in front of the face so the palms lie over the eyes
        const eyeUp = 0.0, eyeFwd = 0.27, eyeSide = 0.06;
        // left hand (character's left = +side)
        let wl = 0, wr = 0;
        if (w.cover > 0.001) {
            L.target.copy(hp).addScaledVector(up, eyeUp).addScaledVector(fwd, eyeFwd).addScaledVector(side, eyeSide);
            R.target.copy(hp).addScaledVector(up, eyeUp).addScaledVector(fwd, eyeFwd).addScaledVector(side, -eyeSide);
            // peek: the right hand drops to the chin
            R.target.addScaledVector(up, -0.13 * w.peek).addScaledVector(side, -0.05 * w.peek);
            L.pole.copy(hp).addScaledVector(up, -0.6).addScaledVector(side, 0.5).addScaledVector(fwd, 0.1);
            R.pole.copy(hp).addScaledVector(up, -0.6).addScaledVector(side, -0.5).addScaledVector(fwd, 0.1);
            wl = wr = w.cover;
        }
        if (w.cheer > 0.001) {
            const ct = cheerStart >= 0 ? (now - cheerStart) / 1000 : 0;
            const pump = Math.sin(ct * Math.PI * 2 * 1.8) * 0.04;
            tmp.copy(hp).addScaledVector(up, 0.42 + pump).addScaledVector(side, 0.3).addScaledVector(fwd, 0.05);
            L.target.lerp(tmp, wl > 0 ? w.cheer : 1);
            tmp.copy(hp).addScaledVector(up, 0.42 - pump).addScaledVector(side, -0.3).addScaledVector(fwd, 0.05);
            R.target.lerp(tmp, wr > 0 ? w.cheer : 1);
            L.pole.copy(hp).addScaledVector(up, -0.2).addScaledVector(side, 0.8).addScaledVector(fwd, -0.3);
            R.pole.copy(hp).addScaledVector(up, -0.2).addScaledVector(side, -0.8).addScaledVector(fwd, -0.3);
            wl = Math.max(wl, w.cheer);
            wr = Math.max(wr, w.cheer);
        }
        if (w.wave > 0.001 && w.cheer < 0.5) {
            const wt = (now - waveFrom) / 1000;
            // hand raised beside the head, the arm holds still, only the palm waves
            tmp.copy(hp).addScaledVector(up, 0.1).addScaledVector(side, -0.4).addScaledVector(fwd, 0.1);
            R.target.copy(wr > 0 ? R.target.lerp(tmp, w.wave) : tmp);
            R.pole.copy(hp).addScaledVector(up, -0.45).addScaledVector(side, -0.9).addScaledVector(fwd, -0.2);
            wr = Math.max(wr, w.wave);
        }
        reach(L, wl);
        reach(R, wr);

        // hands: fingers up (palms on the eyes / waving palm / fists in the air)
        const dirL = new V3(), dirR = new V3();
        if (w.cover > 0.001) {
            dirL.copy(up).addScaledVector(side, -0.25).normalize();
            dirR.copy(up).addScaledVector(side, 0.25).normalize();
            pointHand(B.lh, B.li, dirL, w.cover);
            pointHand(B.rh, B.ri, dirR.lerp(fwd, 0.6 * w.peek).normalize(), w.cover);
        }
        if (w.wave > 0.001 && w.cheer < 0.5) {
            const a = 0.45 * Math.sin((now - waveFrom) / 1000 * Math.PI * 2 * 2.2);
            dirR.copy(up).multiplyScalar(Math.cos(a)).addScaledVector(side, -Math.sin(a)).normalize();
            pointHand(B.rh, B.ri, dirR, w.wave);
        }
        if (w.cheer > 0.001) {
            pointHand(B.lh, B.li, up, w.cheer);
            pointHand(B.rh, B.ri, up, w.cheer);
        }

        // success: jump while cheering, then turn and walk off to the right
        let lift = 0;
        if (cheerStart >= 0) {
            const ct = (now - cheerStart) / 1000;
            // crouch, then a proper jump, twice
            const ph = Math.min(ct, 0.95) / 0.475;
            lift = Math.max(0, Math.sin(ph * Math.PI)) * 0.2 - Math.max(0, -Math.sin(ph * Math.PI)) * 0.02;
        }
        if (walkStart >= 0) {
            const wt = (now - walkStart) / 1000;
            w.run = smooth01(wt / 0.3);
            holder.rotation.y = FACING + (Math.PI / 2 - FACING) * smooth01(wt / 0.3);
            const speed = clips.run ? 3.2 : 1.3;
            holder.position.x = baseX + Math.max(0, wt - 0.15) * speed;
        } else {
            holder.rotation.y = FACING;
        }
        holder.position.y = lift;
        shadow.position.x = holder.position.x;
        shadow.material.opacity = 1 - lift * 5;
        shadow.scale.set(0.95 - lift * 2, 0.6 - lift * 1.2, 1);

        renderer.render(scene, camera);
    }
    raf = requestAnimationFrame(frame);

    /* leave the panel: the canvas becomes a fixed layer from the panel to the
       right edge of the window (same height and scale), above the form */
    function escape() {
        const r = canvas.getBoundingClientRect();
        const bird = stage.querySelector(".pk-stage__bird");
        const br = bird ? bird.getBoundingClientRect() : r;
        const width = Math.max(r.width, window.innerWidth - r.left);
        escaped = { left: r.left, top: r.top, width: width, height: r.height, bird: br };
        canvas.classList.add("is-escaped");
        canvas.style.left = r.left + "px";
        canvas.style.top = r.top + "px";
        canvas.style.width = width + "px";
        canvas.style.height = r.height + "px";
        document.body.appendChild(canvas);
        resize();
    }

    const headScreen = new V3();
    return {
        // seconds of celebration before Odoo opens
        leaveAfter: act.leave ? 2.6 : 1.6,
        setPose(p) {
            if (p !== pose) {
                standUp();
            }
            pose = p;
            if (p === "success" && cheerStart < 0) {
                cheerStart = performance.now();
                setTimeout(() => {
                    cheerStart = -1;
                    if (act.leave) {
                        escape();
                        walkStart = performance.now();
                        act.leave.reset().play();
                        act.idle.crossFadeTo(act.leave, 0.25, false);
                    }
                }, 1000);
            }
        },
        input(g) {
            lookX = g.lookX || 0;
            lookY = g.lookY || 0;
            wander = g.wander || 0;
            if (g.shakeRot && sadStart < 0) {
                sadStart = performance.now();
                standUp();
            }
            if (Math.abs(g.lookX || 0) > 0.05) {
                lastActivity = Math.max(lastActivity, performance.now() - 15000);
            }
        },
        activity() {
            standUp();
        },
        headScreen() {
            B.head.getWorldPosition(headScreen);
            headScreen.project(camera);
            const r = canvas.getBoundingClientRect();
            return {
                x: r.left + (headScreen.x + 1) / 2 * r.width,
                y: r.top + (1 - headScreen.y) / 2 * r.height,
                s: r.height / 1000,
            };
        },
        destroy() {
            cancelAnimationFrame(raf);
            if (ro) {
                ro.disconnect();
            }
            if (login) {
                login.removeEventListener("input", onInput);
            }
            renderer.dispose();
            canvas.remove();
        },
    };
}
