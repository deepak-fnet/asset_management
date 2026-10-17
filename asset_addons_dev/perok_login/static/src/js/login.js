/* ============================================================
   PEROK LOGIN — 3D parrot
   Plain script (not an Odoo module): it is only loaded by the
   login layout. Five pre-rendered 3D poses are stacked in
   .pk-stage__bird (800x1000 px, feet on y=960, body on x=400).

   Renderers
     GL     : the default. Each pose photo is drawn on a deformable
              mesh, so the head turns / tilts toward the cursor, the
              pupils follow it, the eyes blink, the body breathes and a
              pose change swings the head into the next pose.
     images : fallback without WebGL — cross-fades the <img> stack with
              a small squash / lean and the same breathing + parallax.

   Moods → pose
     idle    : nothing focused (looks at the cursor, glances around)
     email   : email field focused (watches the caret)
     shy     : password focused while hidden (wing over the eyes)
     peek    : password revealed with the eye toggle
     success : form submitted (kept while the page navigates)

   Fly-away (successful login): the success pose crouches, then turns
   into a side-view flight sprite (img/parrot/flight.webp + .json, a
   real flight cycle, preloaded once the page is idle) that flies off the
   top-right corner. Without the sprite: GL wing layers flap instead,
   and without WebGL the photo just follows the same path.
   ============================================================ */
(function () {
    "use strict";

    // where this script was loaded from: the 3D module sits next to it
    var SCRIPT_SRC = (document.currentScript && document.currentScript.src) || "";

    var POSES = ["idle", "email", "shy", "peek", "success"];
    var W = 800, H = 1000, FEET_X = 400, FEET_Y = 960;

    /* Rig per pose, in image px. head: ellipse the turn/tilt warps, neck:
       its pivot, eyes: ellipses that blink and whose pupils follow the
       cursor, look: how strongly the pose follows the cursor, lean: which
       way the pose faces (sets the swing direction of a pose change). */
    var RIG = {
        idle: {
            head: [405, 320, 215, 180], neck: [405, 540], look: 1, lean: 0,
            eyes: [[316, 302, 43, 47], [549, 342, 40, 50]],
        },
        email: {
            head: [500, 330, 205, 175], neck: [450, 545], look: 0.65, lean: 1,
            eyes: [[502, 354, 55, 52]],
        },
        shy: {
            head: [410, 360, 215, 185], neck: [410, 600], look: 0.25, lean: -0.4, duck: true,
            eyes: [],
        },
        peek: {
            head: [480, 330, 230, 190], neck: [430, 560], look: 0.45, lean: 1,
            eyes: [[468, 337, 52, 46]],
        },
        success: {
            head: [395, 345, 190, 160], neck: [395, 540], look: 0.3, lean: 0, hop: true,
            eyes: [], wings: [250, 545, 470],
        },
    };

    function noop() {}
    function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
    function spring(v, k, d) { return { v: v, t: v, vel: 0, k: k, d: d }; }
    function settle(s, v) { s.v = s.t = v; s.vel = 0; }
    function integrate(s, dt) {
        s.vel += (s.k * (s.t - s.v) - s.d * s.vel) * dt;
        s.v += s.vel * dt;
    }
    function stepAll(list, dt) {
        for (var t = dt; t > 0; t -= 1 / 120) {
            var h = Math.min(1 / 120, t);
            for (var i = 0; i < list.length; i++) {
                integrate(list[i], h);
            }
        }
    }

    function decode(img) {
        var loaded = img.complete && img.naturalWidth
            ? Promise.resolve()
            : new Promise(function (resolve, reject) {
                img.addEventListener("load", resolve, { once: true });
                img.addEventListener("error", reject, { once: true });
            });
        // decode() can stay pending for images that are not rendered
        // (display:none texture sources): never wait on it for long
        return loaded.then(function () {
            return img.decode ? Promise.race([
                img.decode().catch(noop),
                new Promise(function (resolve) { setTimeout(resolve, 1500); }),
            ]) : null;
        });
    }

    /* ============================================================
       WebGL renderer
       ============================================================ */
    var PAD_X = 64, PAD_Y = 60;     // warp room around the image (matches .pk-gl in CSS)
    var VIEW_W = W + 2 * PAD_X, VIEW_H = H + 2 * PAD_Y;

    var VS = [
        "attribute vec2 a_p;",
        "uniform vec4 u_head; uniform vec2 u_neck;",
        "uniform vec4 u_turn;",      // yaw, pitch, rot (rad), unused
        "uniform vec2 u_ht;",
        "uniform vec4 u_body;",      // lean (rad), sx, sy, ty
        "uniform float u_breath;",
        "uniform vec4 u_wing;",      // left shoulder x, right shoulder x, shoulder y, flap (rad)
        "uniform vec4 u_fly;",       // flight wing layer: shoulder x, y, angle at the shoulder, angle at the tip (rad)
        "uniform vec2 u_flyOpt;",    // 1 = this layer is a wing, span scale (folds on the upstroke)
        "varying vec2 v_uv;",
        "vec2 rot(vec2 d, float a) { float c = cos(a), s = sin(a); return vec2(d.x * c - d.y * s, d.x * s + d.y * c); }",
        "void main() {",
        "  vec2 p = a_p;",
        "  v_uv = a_p / vec2(" + W + ".0, " + H + ".0);",
        // flight: a cut-out wing swings around its shoulder; the tip trails the
        // shoulder (bent feathers) and the span shortens on the upstroke
        "  float isWing = u_flyOpt.x;",
        "  if (isWing > 0.5) {",
        "    vec2 d = p - u_fly.xy;",
        "    float r = clamp(length(d) / 280.0, 0.0, 1.0);",
        "    d.x *= mix(1.0, u_flyOpt.y, r);",
        "    p = u_fly.xy + rot(d, mix(u_fly.z, u_fly.w, r * r));",
        "  }",
        // wings: rotate around the shoulders, fading out toward the body
        "  if (u_wing.w != 0.0) {",
        "    float side = p.x < " + FEET_X + ".0 ? -1.0 : 1.0;",
        "    float sx = side < 0.0 ? u_wing.x : u_wing.y;",
        "    float w = smoothstep(0.0, 70.0, side * (p.x - sx)) * (1.0 - smoothstep(u_wing.z + 260.0, u_wing.z + 420.0, p.y));",
        "    vec2 o = vec2(sx, u_wing.z);",
        "    p = o + rot(p - o, -side * u_wing.w * w);",
        "  }",
        // head: the centre of the face slides further than its rim (reads as a
        // 3D turn), then the whole head tilts around the neck and shifts
        "  vec2 q = (p - u_head.xy) / u_head.zw;",
        "  float wh = (1.0 - smoothstep(0.8, 1.5, length(q))) * (1.0 - isWing);",
        "  float qx = clamp(q.x, -1.0, 1.0), qy = clamp(q.y, -1.0, 1.0);",
        "  p.x += u_turn.x * (1.0 - qx * qx) * 0.2 * u_head.z * wh;",
        "  p.y += u_turn.y * (1.0 - qy * qy) * 0.14 * u_head.w * wh;",
        "  p = u_neck + rot(p - u_neck, u_turn.z * wh);",
        "  p += u_ht * wh;",
        // breathing: chest swells, feet stay planted
        "  float wb = smoothstep(" + FEET_Y + ".0, 560.0, p.y);",
        "  p.x = " + FEET_X + ".0 + (p.x - " + FEET_X + ".0) * (1.0 + 0.012 * u_breath * wb);",
        "  p.y = " + FEET_Y + ".0 + (p.y - " + FEET_Y + ".0) * (1.0 + 0.014 * u_breath);",
        // whole bird: squash, lean and lift around the feet
        "  vec2 f = vec2(" + FEET_X + ".0, " + FEET_Y + ".0);",
        "  p = f + rot((p - f) * u_body.yz, u_body.x) + vec2(0.0, u_body.w);",
        "  vec2 c = (p + vec2(" + PAD_X + ".0, " + PAD_Y + ".0)) / vec2(" + VIEW_W + ".0, " + VIEW_H + ".0);",
        "  gl_Position = vec4(c.x * 2.0 - 1.0, 1.0 - c.y * 2.0, 0.0, 1.0);",
        "}",
    ].join("\n");

    var FS = [
        "precision mediump float;",
        "uniform sampler2D u_tex;",
        "uniform float u_alpha, u_blink, u_tuck;",
        "uniform vec4 u_eye0, u_eye1;",   // cx, cy, rx, ry (rx = 0: no eye)
        "uniform vec2 u_look;",
        "varying vec2 v_uv;",
        "const vec2 SZ = vec2(" + W + ".0, " + H + ".0);",
        "vec2 spx; float lid; float lash; vec3 lcol;",
        "vec3 unpm(vec4 c) { return c.rgb / max(c.a, 0.001); }",
        "void eye(vec4 e, vec2 px) {",
        "  if (e.z <= 0.0) return;",
        "  vec2 q = (px - e.xy) / e.zw;",
        "  float d = length(q);",
        "  if (d >= 1.12) return;",
        // pupil: shift what is drawn inside the eye, pinned at the rim
        "  spx -= u_look * e.zw * 0.28 * (1.0 - smoothstep(0.45, 0.95, d));",
        "  if (u_blink <= 0.0) return;",
        // lids: the upper one sweeps down, the lower one rises a little, they
        // meet on a curved crease; both cover the eye ring too
        "  float bow = 0.24 * (1.0 - q.x * q.x) * u_blink;",
        "  float top = mix(-1.3, 0.3, u_blink) + bow;",
        "  float bot = mix(1.3, 0.3, u_blink) + bow;",
        "  float inside = 1.0 - smoothstep(1.0, 1.12, d);",
        "  float cov = max(smoothstep(top + 0.05, top - 0.05, q.y), smoothstep(bot - 0.05, bot + 0.05, q.y)) * inside;",
        "  if (cov > lid) {",
        "    lid = cov;",
        // lid colour: feathers just above the eye, averaged, warmed, shaded downward
        "    vec2 s = vec2(e.x, e.y - e.w * 1.35) / SZ;",
        "    vec3 f = (unpm(texture2D(u_tex, s)) + unpm(texture2D(u_tex, s - vec2(e.z * 0.5, 0.0) / SZ)) + unpm(texture2D(u_tex, s + vec2(e.z * 0.5, 0.0) / SZ))) / 3.0;",
        "    vec3 skin = mix(f, vec3(0.94, 0.82, 0.52), 0.5);",
        "    lcol = skin * (1.08 - 0.22 * smoothstep(-1.0, top, q.y) * step(q.y, top + 0.05));",
        "  }",
        "  lash = max(lash, (1.0 - smoothstep(0.03, 0.11, abs(q.y - top))) * inside * step(0.04, u_blink));",
        "}",
        "void main() {",
        "  vec2 px = v_uv * SZ;",
        "  spx = px; lid = 0.0; lash = 0.0; lcol = vec3(0.0);",
        "  eye(u_eye0, px); eye(u_eye1, px);",
        "  vec4 c = texture2D(u_tex, spx / SZ);",
        "  c.rgb = mix(c.rgb, lcol * c.a, lid);",
        "  c.rgb = mix(c.rgb, vec3(0.2, 0.1, 0.03) * c.a, lash * 0.8);",
        // in flight the legs are tucked into the belly feathers
        "  c *= 1.0 - u_tuck * smoothstep(845.0, 900.0, v_uv.y * SZ.y);",
        "  gl_FragColor = c * u_alpha;",
        "}",
    ].join("\n");

    function createGL(canvas) {
        var gl = null;
        try {
            var opts = { alpha: true, premultipliedAlpha: true, antialias: true, depth: false, stencil: false };
            gl = canvas.getContext("webgl", opts) || canvas.getContext("experimental-webgl", opts);
        } catch (e) {
            gl = null;
        }
        if (!gl) {
            return null;
        }
        function shader(type, src) {
            var s = gl.createShader(type);
            gl.shaderSource(s, src);
            gl.compileShader(s);
            return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
        }
        var vs = shader(gl.VERTEX_SHADER, VS);
        var fs = shader(gl.FRAGMENT_SHADER, FS);
        if (!vs || !fs) {
            return null;
        }
        var prog = gl.createProgram();
        gl.attachShader(prog, vs);
        gl.attachShader(prog, fs);
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
            return null;
        }
        gl.useProgram(prog);

        // grid mesh over the image
        var CX = 40, CY = 50, verts = [], idx = [];
        for (var y = 0; y <= CY; y++) {
            for (var x = 0; x <= CX; x++) {
                verts.push(x / CX * W, y / CY * H);
            }
        }
        for (y = 0; y < CY; y++) {
            for (x = 0; x < CX; x++) {
                var i = y * (CX + 1) + x;
                idx.push(i, i + 1, i + CX + 1, i + 1, i + CX + 2, i + CX + 1);
            }
        }
        gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.STATIC_DRAW);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(idx), gl.STATIC_DRAW);
        var loc = gl.getAttribLocation(prog, "a_p");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);

        var U = {};
        ["u_head", "u_neck", "u_turn", "u_ht", "u_body", "u_breath", "u_wing", "u_fly", "u_flyOpt",
            "u_tex", "u_alpha", "u_tuck", "u_blink", "u_eye0", "u_eye1", "u_look"].forEach(function (n) {
            U[n] = gl.getUniformLocation(prog, n);
        });
        return { gl: gl, U: U, count: idx.length };
    }

    // power-of-two copy so the texture can be mipmapped (the bird is drawn
    // smaller than its source, plain NPOT sampling would shimmer)
    function uploadTexture(gl, img) {
        var c = document.createElement("canvas");
        c.width = 1024;
        c.height = 1024;
        c.getContext("2d").drawImage(img, 0, 0, 1024, 1024);
        var tex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, c);
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        return tex;
    }

    // flight layers (cut from the success pose): body + each wing with its shoulder
    var FLY = {
        body: { pivot: null },
        wingL: { pivot: [255, 500], sign: 1 },
        wingR: { pivot: [515, 510], sign: -1 },
    };

    /* ============================================================
       Flight sprite: a real side-view flight cycle (atlas + JSON,
       img/parrot/flight.*). Loaded after the page has painted; when it is
       missing or broken the fly-away uses the GL wing layers above.
       ============================================================ */
    var ASSET_V = "19.2.0";
    var SPRITE_ENABLED = false;
    var SPRITE_SRC = "/perok_login/static/src/img/parrot/flight";
    // success pose: body centre (chest) and body width, in image px
    var BODY = { x: 385, y: 600, w: 280 };
    var SIDE_LENGTH = 1.3;      // a side view is this much longer than the front view is wide

    function intList(list, count) {
        return Array.isArray(list) ? list.filter(function (i) {
            return i === Math.floor(i) && i >= 0 && i < count;
        }) : [];
    }

    // validates flight.json; null when it cannot be played
    function readSprite(m) {
        if (!m || typeof m !== "object") {
            return null;
        }
        var fw = +m.fw, fh = +m.fh, cols = +m.cols, count = +m.count;
        if (!(fw > 0 && fh > 0 && cols >= 1 && count >= 1)) {
            return null;
        }
        cols = Math.floor(cols);
        count = Math.floor(count);
        var loop = intList(m.loop, count);
        if (!loop.length) {
            return null;
        }
        var anchor = Array.isArray(m.anchor) && m.anchor.length === 2 && isFinite(m.anchor[0]) && isFinite(m.anchor[1])
            ? [+m.anchor[0], +m.anchor[1]] : [fw / 2, fh / 2];
        return {
            fw: fw, fh: fh, cols: cols, count: count,
            fps: clamp(+m.fps || 24, 6, 60),
            takeoff: intList(m.takeoff, count),
            loop: loop,
            anchor: anchor,
            bodyWidth: +m.bodyWidth > 0 ? +m.bodyWidth : fw * 0.4,
            url: SPRITE_SRC + ".webp?v=" + ASSET_V,
            up: null,
        };
    }

    /* How high the wings are in every frame (0 = fully down, 1 = fully up),
       read from the atlas alpha: wings up push the top edge of the frame's
       silhouette above the anchor, wings down push the bottom edge below
       it. Drives the body bob. Leaves sp.up null if it cannot be read. */
    function measureWings(sp, img) {
        try {
            var k = Math.min(1, 160 / sp.fw);
            var cw = Math.max(1, Math.round(sp.aw * k)), ch = Math.max(1, Math.round(sp.ah * k));
            var c = document.createElement("canvas");
            c.width = cw;
            c.height = ch;
            var ctx = c.getContext("2d", { willReadFrequently: true });
            ctx.drawImage(img, 0, 0, cw, ch);
            var data = ctx.getImageData(0, 0, cw, ch).data;
            var w = Math.floor(sp.fw * k), h = Math.floor(sp.fh * k), ay = sp.anchor[1] * k;
            var raw = [];
            for (var i = 0; i < sp.count; i++) {
                var x0 = Math.round((i % sp.cols) * sp.fw * k), y0 = Math.round(Math.floor(i / sp.cols) * sp.fh * k);
                var top = -1, bot = -1;
                for (var y = 0; y < h && y0 + y < ch; y++) {
                    for (var x = 0; x < w && x0 + x < cw; x++) {
                        if (data[((y0 + y) * cw + x0 + x) * 4 + 3] > 100) {
                            if (top < 0) {
                                top = y;
                            }
                            bot = y;
                            break;
                        }
                    }
                }
                raw.push(top < 0 ? null : (ay - top) - (bot - ay));
            }
            var used = sp.loop.concat(sp.takeoff).map(function (i) { return raw[i]; })
                .filter(function (v) { return v !== null; });
            var lo = Math.min.apply(null, used), hi = Math.max.apply(null, used);
            if (!used.length || hi - lo < 2) {
                return;
            }
            sp.up = raw.map(function (v) { return v === null ? 0.5 : (v - lo) / (hi - lo); });
        } catch (e) {
            sp.up = null;
        }
    }

    // resolves the sprite, or null (missing files, bad JSON, undecodable
    // image): never rejects
    function loadSprite() {
        // disabled: the only flight sheet available shows a different bird
        if (!SPRITE_ENABLED || !window.fetch || !window.Promise) {
            return Promise.resolve(null);
        }
        return fetch(SPRITE_SRC + ".json?v=" + ASSET_V, { credentials: "same-origin" }).then(function (res) {
            return res.ok ? res.json() : null;
        }).then(function (meta) {
            var sp = readSprite(meta);
            if (!sp) {
                return null;
            }
            var img = new Image();
            img.decoding = "async";
            img.src = sp.url;
            return decode(img).then(function () {
                sp.aw = img.naturalWidth;
                sp.ah = img.naturalHeight;
                var rows = Math.ceil(sp.count / sp.cols);
                if (sp.aw + 1 < Math.min(sp.count, sp.cols) * sp.fw || sp.ah + 1 < rows * sp.fh) {
                    return null;
                }
                sp.img = img;     // keeps the decoded atlas alive for the flyer's background
                measureWings(sp, img);
                return sp;
            });
        }).catch(function () { return null; });
    }

    function GLRenderer(stage, bird, imgs, reduced, flyImgs) {
        var canvas = document.createElement("canvas");
        canvas.className = "pk-gl";
        canvas.setAttribute("aria-hidden", "true");
        bird.appendChild(canvas);
        var ctx = createGL(canvas);
        if (!ctx) {
            canvas.remove();
            return null;
        }
        var gl = ctx.gl, U = ctx.U;
        var lost = false;
        var onLost = null;
        canvas.addEventListener("webglcontextlost", function (e) {
            e.preventDefault();
            lost = true;
            if (onLost) {
                onLost();
            }
        });

        var L = {};
        POSES.forEach(function (name) {
            L[name] = {
                tex: null, alpha: name === "idle" ? 1 : 0, on: name === "idle", hold: 0,
                yaw: spring(0, 170, 14), pitch: spring(0, 170, 14), rot: spring(0, 170, 14),
                sx: spring(1, 190, 15), sy: spring(1, 190, 15), ty: spring(0, 150, 12),
            };
        });
        var order = ["idle"];
        var current = "idle";
        var flyTex = null;

        // resolves false when a texture cannot be made (e.g. a cross-origin
        // image taints it); the controller then falls back to the <img>s
        function load() {
            return Promise.all(POSES.map(function (name) {
                return decode(imgs[name]).then(function () {
                    try {
                        L[name].tex = uploadTexture(gl, imgs[name]);
                    } catch (e) {
                        L[name].tex = null;
                    }
                    return !!L[name].tex;
                }, function () { return false; });
            })).then(function (ok) {
                loadFlight();
                return ok.every(Boolean);
            });
        }

        // optional: without them the flight uses the whole success pose
        function loadFlight() {
            var parts = Object.keys(FLY);
            if (!flyImgs || parts.some(function (n) { return !flyImgs[n]; })) {
                return;
            }
            // fresh Image objects: browsers may never fetch the hidden (display:none) <img>s
            var src = {};
            parts.forEach(function (n) {
                src[n] = new Image();
                src[n].src = flyImgs[n].currentSrc || flyImgs[n].src;
            });
            Promise.all(parts.map(function (n) { return decode(src[n]); })).then(function () {
                var t = {};
                try {
                    parts.forEach(function (n) { t[n] = uploadTexture(gl, src[n]); });
                    flyTex = t;
                } catch (e) {
                    flyTex = null;
                }
            }, noop);
        }

        function resize() {
            var r = canvas.getBoundingClientRect();
            var dpr = Math.min(window.devicePixelRatio || 1, 2);
            var w = Math.max(1, Math.round(r.width * dpr));
            var h = Math.max(1, Math.round(r.height * dpr));
            if (canvas.width !== w || canvas.height !== h) {
                canvas.width = w;
                canvas.height = h;
                gl.viewport(0, 0, w, h);
            }
        }

        // screen position of a point given in image px
        function toScreen(x, y) {
            var r = canvas.getBoundingClientRect();
            return {
                x: r.left + (x + PAD_X) / VIEW_W * r.width,
                y: r.top + (y + PAD_Y) / VIEW_H * r.height,
                s: r.width / VIEW_W,
            };
        }

        function has(name) { return !!L[name].tex; }

        function setPose(to) {
            var from = current;
            if (to === from || !L[to].tex) {
                return;
            }
            current = to;
            var a = L[from], b = L[to];
            if (order.indexOf(to) >= 0) {
                order.splice(order.indexOf(to), 1);
            }
            order.push(to);
            if (reduced) {
                a.on = false; a.alpha = 0; b.on = true; b.alpha = 1;
                return;
            }
            var dir = Math.sign(RIG[to].lean - RIG[from].lean) || (to === "success" ? 0 : 1);
            // outgoing: swings its head toward the new pose and squashes while it fades
            a.on = false;
            a.hold = 0.06;
            a.yaw.t = dir * 0.95;
            a.rot.t = dir * 0.1;
            a.sx.t = 1.012;
            a.sy.t = RIG[to].duck ? 0.955 : 0.975;
            // incoming: starts turned back toward where we came from, springs into place
            b.on = true;
            b.hold = 0;
            if (b.alpha < 0.05) {
                b.yaw.v = -dir * 0.8;
                b.rot.v = -dir * 0.085;
                b.pitch.v = RIG[to].duck ? 0.6 : 0.15;
                b.sx.v = 1.015;
                b.sy.v = RIG[to].duck ? 0.95 : 0.965;
                b.ty.v = RIG[to].duck ? 10 : 5;
                b.yaw.vel = b.rot.vel = b.pitch.vel = b.sx.vel = b.sy.vel = b.ty.vel = 0;
            } else {
                b.yaw.vel -= dir * 4;
            }
            if (RIG[to].hop) {
                b.ty.vel = -520;
            }
            b.yaw.t = b.rot.t = b.pitch.t = b.ty.t = 0;
            b.sx.t = b.sy.t = 1;
        }

        function draw(name, g) {
            var l = L[name], r = RIG[name];
            if (!l.tex || l.alpha <= 0) {
                return;
            }
            var k = r.look;
            var yaw = l.yaw.v + g.lookX * 0.8 * k + g.wander * k + g.shakeYaw;
            var pitch = l.pitch.v + g.lookY * 0.6 * k;
            var rot = l.rot.v + g.lookX * 0.09 * k + g.shakeRot + g.bob;
            gl.bindTexture(gl.TEXTURE_2D, l.tex);
            gl.uniform1i(U.u_tex, 0);
            gl.uniform4f(U.u_head, r.head[0], r.head[1], r.head[2], r.head[3]);
            gl.uniform2f(U.u_neck, r.neck[0], r.neck[1]);
            gl.uniform4f(U.u_turn, yaw, pitch, rot, 0);
            gl.uniform2f(U.u_ht, g.lookX * 14 * k, g.lookY * 9 * k);
            gl.uniform4f(U.u_body, g.lean, l.sx.v, l.sy.v, l.ty.v);
            gl.uniform1f(U.u_breath, g.breath);
            if (r.wings) {
                gl.uniform4f(U.u_wing, r.wings[0], r.wings[1], r.wings[2], g.flap);
            } else {
                gl.uniform4f(U.u_wing, 0, 0, 0, 0);
            }
            var e0 = r.eyes[0] || [0, 0, 0, 0], e1 = r.eyes[1] || [0, 0, 0, 0];
            gl.uniform4f(U.u_eye0, e0[0], e0[1], e0[2], e0[3]);
            gl.uniform4f(U.u_eye1, e1[0], e1[1], e1[2], e1[3]);
            gl.uniform2f(U.u_look, g.eyeX, g.eyeY);
            gl.uniform1f(U.u_blink, g.blink);
            var o = l.alpha * l.alpha * (3 - 2 * l.alpha);
            gl.uniform1f(U.u_alpha, o);
            gl.uniform1f(U.u_tuck, 0);
            gl.uniform2f(U.u_flyOpt, 0, 1);
            gl.drawElements(gl.TRIANGLES, ctx.count, gl.UNSIGNED_SHORT, 0);
        }

        // in flight: both wings, then the body over their roots
        function drawFlight(g) {
            var r = RIG.success, f = g.fly;
            gl.uniform4f(U.u_head, r.head[0], r.head[1], r.head[2], r.head[3]);
            gl.uniform2f(U.u_neck, r.neck[0], r.neck[1]);
            gl.uniform4f(U.u_turn, g.lookX * 0.25, g.lookY * 0.2, g.lookX * 0.04, 0);
            gl.uniform2f(U.u_ht, 0, 0);
            gl.uniform4f(U.u_body, f.tilt, f.sx, f.sy, f.ty);
            gl.uniform1f(U.u_breath, 0);
            gl.uniform4f(U.u_wing, 0, 0, 0, 0);
            gl.uniform4f(U.u_eye0, 0, 0, 0, 0);
            gl.uniform4f(U.u_eye1, 0, 0, 0, 0);
            gl.uniform2f(U.u_look, 0, 0);
            gl.uniform1f(U.u_blink, 0);
            gl.uniform1f(U.u_alpha, 1);
            gl.uniform1f(U.u_tuck, f.tuck || 0);
            gl.uniform1i(U.u_tex, 0);
            ["wingL", "wingR", "body"].forEach(function (n) {
                var part = FLY[n];
                gl.bindTexture(gl.TEXTURE_2D, flyTex[n]);
                if (part.pivot) {
                    gl.uniform4f(U.u_fly, part.pivot[0], part.pivot[1], part.sign * f.base, part.sign * f.tip);
                    gl.uniform2f(U.u_flyOpt, 1, f.fold);
                } else {
                    gl.uniform2f(U.u_flyOpt, 0, 1);
                }
                gl.drawElements(gl.TRIANGLES, ctx.count, gl.UNSIGNED_SHORT, 0);
            });
        }

        function frame(dt, g) {
            if (lost) {
                return;
            }
            POSES.forEach(function (name) {
                var l = L[name];
                if (l.on) {
                    l.alpha = Math.min(1, l.alpha + dt / 0.16);
                } else if (l.hold > 0) {
                    l.hold -= dt;
                } else if (l.alpha > 0) {
                    l.alpha = Math.max(0, l.alpha - dt / 0.16);
                    if (l.alpha === 0) {
                        settle(l.yaw, 0); settle(l.pitch, 0); settle(l.rot, 0);
                        settle(l.sx, 1); settle(l.sy, 1); settle(l.ty, 0);
                    }
                }
                if (l.alpha > 0 || l.on) {
                    stepAll([l.yaw, l.pitch, l.rot, l.sx, l.sy, l.ty], dt);
                }
            });
            resize();
            gl.clearColor(0, 0, 0, 0);
            gl.clear(gl.COLOR_BUFFER_BIT);
            if (g.fly && flyTex) {
                drawFlight(g);
                return;
            }
            order = order.filter(function (n) { return L[n].alpha > 0 || L[n].on; });
            for (var i = 0; i < order.length; i++) {
                draw(order[i], g);
            }
        }

        return {
            load: load, setPose: setPose, frame: frame, has: has, toScreen: toScreen,
            current: function () { return current; },
            lift: function () { return clamp(-L[current].ty.v / 40, 0, 1); },
            onLost: function (fn) { onLost = fn; },
            destroy: function () { canvas.remove(); },
        };
    }

    /* ============================================================
       <img> renderer (fallback)
       ============================================================ */
    function ImageRenderer(stage, bird, imgs, reduced) {
        var P = {};
        var zTop = 1;
        POSES.forEach(function (name) {
            P[name] = {
                el: imgs[name], ok: false, on: name === "idle", op: name === "idle" ? 1 : 0, hold: 0, shown: null,
                sx: spring(1, 180, 15), sy: spring(1, 180, 15), rot: spring(0, 180, 15), ty: spring(0, 180, 15),
            };
        });
        P.idle.el.style.zIndex = zTop;
        var current = "idle";

        function load() {
            return Promise.all(POSES.map(function (name) {
                return decode(P[name].el).then(function () { P[name].ok = true; }, noop);
            }));
        }

        function setPose(to) {
            var from = current;
            if (to === from || !P[to].ok) {
                return;
            }
            current = to;
            var a = P[from], b = P[to];
            a.el.classList.remove("is-on");
            b.el.classList.add("is-on");
            if (reduced) {
                a.on = false; a.op = 0; b.on = true; b.op = 1;
                return;
            }
            var dir = Math.sign(RIG[to].lean - RIG[from].lean);
            a.on = false;
            a.hold = 0.08;
            a.rot.t = dir * 2.6;
            a.sx.t = 1.012;
            a.sy.t = RIG[to].duck ? 0.96 : 0.975;
            a.ty.t = RIG[to].hop ? -0.6 : 0.8;
            b.on = true;
            b.hold = 0;
            b.el.style.zIndex = ++zTop;
            if (b.op < 0.05) {
                b.rot.v = dir * 2.2;
                b.sx.v = 1.014;
                b.sy.v = RIG[to].duck ? 0.95 : 0.97;
                b.ty.v = RIG[to].duck ? 1.6 : 1;
                b.rot.vel = b.sx.vel = b.sy.vel = b.ty.vel = 0;
            } else {
                b.rot.vel += dir * 30;
            }
            if (RIG[to].hop) {
                b.ty.vel = -62;
            }
            b.rot.t = b.ty.t = 0;
            b.sx.t = b.sy.t = 1;
        }

        function paint(p) {
            if (p.op <= 0 && !p.on) {
                if (p.shown !== false) {
                    p.el.style.opacity = "0";
                    p.el.style.visibility = "hidden";
                    p.el.style.transform = "";
                    p.shown = false;
                }
                return;
            }
            var o = p.op * p.op * (3 - 2 * p.op);
            p.el.style.opacity = o.toFixed(3);
            p.el.style.visibility = "visible";
            p.el.style.transform = "translateY(" + p.ty.v.toFixed(3) + "%) rotate(" + p.rot.v.toFixed(3) +
                "deg) scale(" + p.sx.v.toFixed(4) + "," + p.sy.v.toFixed(4) + ")";
            p.shown = true;
        }

        function frame(dt, g) {
            POSES.forEach(function (name) {
                var p = P[name];
                if (p.on) {
                    p.op = Math.min(1, p.op + dt / 0.26);
                } else if (p.hold > 0) {
                    p.hold -= dt;
                } else if (p.op > 0) {
                    p.op = Math.max(0, p.op - dt / 0.3);
                    if (p.op === 0) {
                        settle(p.sx, 1); settle(p.sy, 1); settle(p.rot, 0); settle(p.ty, 0);
                    }
                }
                if (p.op > 0 || p.on) {
                    stepAll([p.sx, p.sy, p.rot, p.ty], dt);
                }
                paint(p);
            });
            if (g.fly) {
                return;
            }
            // the photo cannot bend here: breathe / sway the whole bird instead
            bird.style.transform = "rotate(" + (g.lean * 57.3 + g.shakeRot * 40).toFixed(3) + "deg) scale(" +
                (1 - g.breath * 0.004).toFixed(4) + "," + (1 + g.breath * 0.012).toFixed(4) + ")";
        }

        function toScreen(x, y) {
            var r = bird.getBoundingClientRect();
            return { x: r.left + x / W * r.width, y: r.top + y / H * r.height, s: r.width / W };
        }

        return {
            load: load, setPose: setPose, frame: frame, toScreen: toScreen,
            has: function (name) { return P[name].ok; },
            current: function () { return current; },
            lift: function () { return clamp(-P[current].ty.v / 4, 0, 1); },
            onLost: noop, destroy: noop,
        };
    }

    /* ============================================================
       Controller: form state, cursor, timing
       ============================================================ */
    /* ============================================================
       Video renderer: pre-rendered Blender clips (static/src/video/,
       VP9 WebM with alpha, described by clips.json). Used when the
       clips exist and the browser can show transparent WebM; the
       renderers above stay as the fallback.
         idle            : loop (blink, preening)
         email / peek    : look_in, held on its last frame; look_out back
         shy             : cover_in (a wing over the eyes); cover_out back
         fly             : played in place while the controller moves the
                           whole bird along the flight path
       ============================================================ */
    var VIDEO_BASE = "/perok_login/static/src/video/";
    var VIDEO_V = "19.2.0";
    var VIDEO_GOAL = { idle: "idle", email: "look", peek: "look", shy: "cover", success: "idle" };

    function videoSupported() {
        var v = document.createElement("video");
        if (!v.canPlayType || !v.canPlayType('video/webm; codecs="vp9"')) {
            return false;
        }
        // Safari plays VP9 but drops the alpha channel (black box)
        var ua = navigator.userAgent;
        return !(/Safari\//.test(ua) && !/(Chrome|Chromium|Edg|OPR|Firefox)\//.test(ua));
    }

    function VideoRenderer(stage, bird, meta) {
        var box = document.createElement("div");
        box.className = "pk-vid";
        box.setAttribute("aria-hidden", "true");
        bird.appendChild(box);

        var MW = meta.width || W, MH = meta.height || H;
        var V = {};
        Object.keys(meta.clips).forEach(function (name) {
            var c = meta.clips[name];
            var v = document.createElement("video");
            v.muted = true;
            v.defaultMuted = true;
            v.playsInline = true;
            v.setAttribute("playsinline", "");
            v.setAttribute("muted", "");
            v.preload = "auto";
            v.loop = !!c.loop;
            v.src = VIDEO_BASE + c.file + "?v=" + VIDEO_V;
            // place the clip on the 800x1000 pose canvas (feet on y=960, body on x=400)
            var a = c.anchor || meta.anchor || {};
            var cw = (c.width || MW) * W / MW, ch = (c.height || MH) * H / MH;
            var left = FEET_X - (a.centerX != null ? a.centerX : (c.width || MW) / 2) * W / MW;
            var top = FEET_Y - (a.feetY != null ? a.feetY : (c.height || MH) * 0.96) * H / MH;
            v.style.left = (left / W * 100) + "%";
            v.style.top = (top / H * 100) + "%";
            v.style.width = (cw / W * 100) + "%";
            v.style.height = (ch / H * 100) + "%";
            box.appendChild(v);
            V[name] = v;
        });

        var pose = "idle";      // the form state we were asked to show
        var goal = "idle";      // idle | look | cover | fly
        var vs = "idle";        // what is on screen once the running clip ends
        var shown = null;
        var busy = false;       // an in/out clip is playing
        var flying = false;
        var dead = false;

        function show(v) {
            if (shown === v) {
                return;
            }
            var prev = shown;
            shown = v;
            v.classList.add("is-on");
            if (prev) {
                prev.classList.remove("is-on");
                if (prev !== V.idle || !flying) {
                    prev.pause();
                }
            }
        }

        // start a clip and reveal it on its first decoded frame (no blank flash)
        function play(name, onEnd) {
            var v = V[name];
            if (!v || dead) {
                return false;
            }
            if (!v.loop) {
                try { v.currentTime = 0; } catch (e) { /* not seekable yet */ }
            }
            var revealed = false;
            function reveal() {
                if (!revealed) {
                    revealed = true;
                    show(v);
                }
            }
            v.onplaying = reveal;
            v.onended = function () {
                v.onended = null;
                if (onEnd) {
                    onEnd();
                }
            };
            var p = v.play();
            if (p && p.catch) {
                p.catch(function () {
                    // autoplay refused or decode error: jump straight to the end state
                    reveal();
                    if (onEnd) {
                        onEnd();
                    }
                });
            }
            setTimeout(reveal, 250);
            return true;
        }

        function step() {
            if (busy || flying || dead) {
                return;
            }
            if (vs === goal) {
                if (vs === "idle" && (shown !== V.idle || V.idle.paused)) {
                    play("idle");
                }
                return;
            }
            var clip, after;
            if (vs === "look") {
                clip = "look_out"; after = "idle";
            } else if (vs === "cover") {
                clip = "cover_out"; after = "idle";
            } else if (goal === "look") {
                clip = "look_in"; after = "look";
            } else {
                clip = "cover_in"; after = "cover";
            }
            if (!V[clip]) {
                vs = after;
                step();
                return;
            }
            busy = true;
            play(clip, function () {
                busy = false;
                vs = after;
                step();
            });
        }

        function load() {
            var v = V.idle;
            return new Promise(function (resolve) {
                if (v.readyState >= 3) {
                    resolve(true);
                    return;
                }
                v.addEventListener("canplay", function () { resolve(true); }, { once: true });
                v.addEventListener("error", function () { resolve(false); }, { once: true });
                setTimeout(function () { resolve(false); }, 6000);
            });
        }

        return {
            kind: "video",
            load: load,
            has: function (name) { return POSES.indexOf(name) >= 0; },
            current: function () { return pose; },
            setPose: function (to) {
                pose = to;
                goal = VIDEO_GOAL[to] || "idle";
                step();
            },
            begin: function () { step(); },
            // the fly clip starts from the neutral pose: true once nothing else is playing
            readyToFly: function () { return !busy && vs === "idle"; },
            // seconds of crouch/take-off at the start of the fly clip, before it travels
            takeoff: (meta.clips.fly && meta.clips.fly.takeoff) || 0.45,
            frame: function (dt, g) {
                if (g.fly) {
                    if (!flying && V.fly) {
                        flying = true;
                        play("fly");
                    }
                    return;
                }
                // error head-shake: the clips cannot do it, rock the whole bird
                bird.style.transform = g.shakeRot ? "rotate(" + (g.shakeRot * 40).toFixed(3) + "deg)" : "";
            },
            toScreen: function (x, y) {
                var r = bird.getBoundingClientRect();
                return { x: r.left + x / W * r.width, y: r.top + y / H * r.height, s: r.width / W };
            },
            lift: function () { return 0; },
            onLost: noop,
            destroy: function () {
                dead = true;
                Object.keys(V).forEach(function (n) { V[n].pause(); });
                box.remove();
            },
        };
    }

    /* ============================================================
       Uploaded video mascot (Settings > Login Page > Animations).
       clips: {idle, look, eyes_close, eyes_open, happy, sad} -> URL,
       only idle is required. The form drives it like the parrot:
         idle             : idle loops
         email            : look (once, last frame held)
         password focused : eyes_close (held); eyes_open when leaving
         login success    : happy (held while Odoo opens)
         login failure    : sad, then back to idle
       ============================================================ */
    function HumanRenderer(stage, bird, clips) {
        var box = document.createElement("div");
        box.className = "pk-vid pk-vid--human";
        box.setAttribute("aria-hidden", "true");
        bird.appendChild(box);

        // still images (one pose per state) instead of clips: shown for a moment each
        var STILL = { look: 0.35, eyes_close: 0.25, eyes_open: 0.2, happy: 0.3, sad: 1.8 };
        var images = clips._images || [];
        var V = {};
        Object.keys(clips).forEach(function (name) {
            if (name.charAt(0) === "_") {
                return;
            }
            if (images.indexOf(name) >= 0) {
                var im = document.createElement("img");
                im.alt = "";
                im.draggable = false;
                im.decoding = "async";
                im.src = clips[name];
                im.isStill = true;
                im.pause = noop;
                if (name === "idle") {
                    im.className = "is-breath";
                }
                box.appendChild(im);
                V[name] = im;
                return;
            }
            var v = document.createElement("video");
            v.muted = true;
            v.defaultMuted = true;
            v.playsInline = true;
            v.setAttribute("playsinline", "");
            v.setAttribute("muted", "");
            // the idle clip first; the others start loading once it plays
            v.preload = name === "idle" ? "auto" : "none";
            v.loop = name === "idle";
            v.src = clips[name];
            box.appendChild(v);
            V[name] = v;
        });

        // per clip: speed, and the part that repeats while the state lasts
        var CFG = clips._cfg || {};
        function cfg(name) { return CFG[name] || { speed: 1, from: 0, to: 0 }; }
        function loops(name) {
            var c = cfg(name);
            return name === "idle" || c.from > 0 || c.to > 0;
        }
        var STATE_CLIP = { look: "look", closed: "eyes_close", open: "eyes_open", happy: "happy" };

        var GOAL = {
            idle: "idle",
            peek: V.eyes_open ? "open" : (V.eyes_close ? "closed" : "idle"),
            email: V.look ? "look" : "idle",
            shy: V.eyes_close ? "closed" : "idle",
            success: V.happy ? "happy" : "idle",
        };
        var acted = false;
        function act(e) {
            if (!acted && e.isTrusted !== false) {
                acted = true;
                if (pose === "email") {
                    api.setPose("email");
                }
            }
        }
        document.addEventListener("pointerdown", act, true);
        document.addEventListener("keydown", act, true);
        var FADE = 0;           // ms: clips switch instantly (no cross-fade)
        var current = null;     // clip playing with an onEnd still pending
        var pose = "idle";
        var goal = "idle";
        var vs = "idle";        // state shown once the running clip ends
        var shown = null;
        var busy = false;
        var sadUntil = 0;
        var token = 0;
        var blinkAt = 0, blinkEnd = 0;

        /* success clip that runs: lift the clip out of its panel into a fixed
           layer above the page (same place and size) and move it to the right,
           over the form and off the screen, starting when the run starts */
        var run = clips._run || null;     // {delay: s, speed: px/s, drop: {url, t, rect}}

        // the item the clip loses: appears where it was, falls to the floor and
        // stays in the panel (r: the clip's box before it starts moving)
        function dropItem(r) {
            var d = run.drop, v = V.happy;
            var vw = v.videoWidth || 720, vh = v.videoHeight || 1280;
            var k = Math.min(r.width / vw, r.height / vh);       // object-fit: contain
            var ox = (r.width - vw * k) / 2, oy = r.height - vh * k;  // bottom aligned
            var br = bird.getBoundingClientRect();
            var im = document.createElement("img");
            im.className = "pk-drop";
            im.alt = "";
            im.src = d.url;
            im.style.left = (r.left - br.left + ox + d.rect[0] * k).toFixed(1) + "px";
            im.style.top = (r.top - br.top + oy + d.rect[1] * k).toFixed(1) + "px";
            im.style.width = (d.rect[2] * k).toFixed(1) + "px";
            // fall from where it was to the clip's floor line
            var fall = Math.max(0, (vh * 0.95 - (d.rect[1] + d.rect[3])) * k);
            im.style.setProperty("--pk-fall", fall.toFixed(1) + "px");
            setTimeout(function () {
                bird.appendChild(im);
                im.classList.add("is-falling");
            }, d.t * 1000);
        }
        function runAcross() {
            var r = box.getBoundingClientRect();
            var layer = document.createElement("div");
            layer.className = "pk-runlayer";
            document.body.appendChild(layer);
            box.style.left = r.left + "px";
            box.style.top = r.top + "px";
            box.style.width = r.width + "px";
            box.style.height = r.height + "px";
            box.classList.add("is-running");
            layer.appendChild(box);
            if (run.drop) {
                dropItem(r);
            }
            // run to the card's right edge and no further: stays on screen
            var card = document.querySelector(".pk-card");
            var right = Math.min(window.innerWidth - 8, card ? card.getBoundingClientRect().right : window.innerWidth);
            var dist = Math.max(0, right - r.right + r.width * 0.12);
            // spread the run over what is left of the clip, so the steps match the travel
            var d = V.happy.duration / (cfg("happy").speed || 1);
            var T = Math.max(0.6, (isFinite(d) && d > 0 ? d : 3) - run.delay);
            var t0 = performance.now() + run.delay * 1000;
            (function move(now) {
                var u = clamp((now - t0) / 1000 / T, 0, 1);
                // quick start, then a steady pace
                var x = dist * (u < 0.2 ? u * u * 2.5 : u * 1.125 - 0.125);
                box.style.transform = "translateX(" + x.toFixed(1) + "px)";
                if (u < 1) {
                    requestAnimationFrame(move);
                }
            })(performance.now());
        }

        var zTop = 1;
        function show(v) {
            if (shown === v) {
                return;
            }
            var prev = shown;
            shown = v;
            // the incoming clip goes on top; the outgoing one is hidden right after
            v.style.zIndex = ++zTop;
            v.classList.add("is-on");
            if (prev) {
                setTimeout(function () {
                    if (shown !== prev) {
                        prev.classList.remove("is-on");
                        prev.pause();
                    }
                }, FADE + 40);
            }
        }

        // start a clip, reveal it on its first frame; onEnd for non-looping clips
        function play(name, onEnd) {
            var v = V[name];
            var my = ++token;
            current = onEnd ? name : null;
            if (!v) {
                if (onEnd) {
                    onEnd();
                }
                return;
            }
            if (v.isStill) {
                show(v);
                if (onEnd) {
                    setTimeout(function () {
                        if (my === token) {
                            onEnd();
                        }
                    }, (STILL[name] || 0.3) * 1000);
                }
                return;
            }
            var c = cfg(name);
            v.playbackRate = c.speed || 1;
            var lf = c.from || 0, lt = c.to || 0;
            // the idle clip without a window repeats whole; a window repeats [from, to]
            v.loop = name === "idle" && !lf && !lt;
            if (!v.loop) {
                try { v.currentTime = 0; } catch (e) { /* not seekable yet */ }
            }
            if (!onEnd && (lf || lt) && !v.loop) {
                (function tick() {
                    if (my !== token) {
                        return;
                    }
                    var end = lt > 0 ? Math.min(lt, v.duration || lt) : v.duration;
                    if (v.ended || (end && v.currentTime >= end - 0.02)) {
                        v.currentTime = Math.min(lf, (v.duration || lf) - 0.05);
                        if (v.paused) {
                            v.play();
                        }
                    }
                    requestAnimationFrame(tick);
                })();
            }
            var revealed = false;
            function reveal() {
                if (!revealed && my === token) {
                    revealed = true;
                    show(v);
                }
            }
            v.onplaying = reveal;
            v.onended = function () {
                v.onended = null;
                if (my === token && onEnd) {
                    onEnd();
                }
            };
            var pr = v.play();
            if (pr && pr.catch) {
                pr.catch(function () {
                    reveal();
                    if (my === token && onEnd) {
                        onEnd();
                    }
                });
            }
            setTimeout(reveal, 300);
        }

        function step() {
            if (busy) {
                return;
            }
            if (vs === goal) {
                if (vs === "idle" && (shown !== V.idle || (!V.idle.isStill && V.idle.paused))) {
                    play("idle");
                }
                return;
            }
            if (goal === "idle") {
                vs = "idle";
                play("idle");
                return;
            }
            var clip = STATE_CLIP[goal];
            if (clip === "happy" && run && V.happy && !V.happy.isStill) {
                runAcross();
            }
            if (V[clip] && !V[clip].isStill && loops(clip)) {
                // a looping state: shown at once, repeats until the form changes
                vs = goal;
                play(clip);
                return;
            }
            // plays once and holds its last frame
            busy = true;
            var g = goal;
            play(clip, function () {
                busy = false;
                vs = g;
                step();
            });
        }

        function sad() {
            if (!V.sad || goal === "happy") {
                return;
            }
            busy = true;
            play("sad", function () { busy = false; vs = "idle"; step(); });
        }

        function load() {
            var v = V.idle;
            return new Promise(function (resolve) {
                if (!v) {
                    resolve(false);
                    return;
                }
                if (v.isStill) {
                    decode(v).then(function () { resolve(true); }, function () { resolve(false); });
                    return;
                }
                function rest() {
                    Object.keys(V).forEach(function (n) {
                        var c = V[n];
                        if (n !== "idle" && !c.isStill && c.preload !== "auto") {
                            c.preload = "auto";
                            // load() would restart a clip that is already playing
                            if (c.paused && c.readyState === 0 && c !== shown) {
                                c.load();
                            }
                        }
                    });
                }
                v.addEventListener("canplaythrough", rest, { once: true });
                setTimeout(rest, 2500);
                if (v.readyState >= 3) {
                    resolve(true);
                    return;
                }
                v.addEventListener("canplay", function () { resolve(true); }, { once: true });
                v.addEventListener("error", function () { resolve(false); }, { once: true });
                setTimeout(function () { resolve(true); }, 6000);
            });
        }

        var api = {
            kind: "video",
            load: load,
            has: function () { return true; },
            current: function () { return pose; },
            setPose: function (to) {
                pose = to;
                var was = goal;
                // the email field is focused on page load: stay idle until the
                // user really clicks or types
                goal = to === "email" && !acted ? "idle" : GOAL[to] || "idle";
                if (goal === was) {
                    step();     // nothing new: just make sure something is playing
                    return;
                }
                // follow the form at once: a running look / eyes-open clip (or the
                // held eyes when logging in) gives way, the cross-fade hides the cut
                if (busy && (current === "look" || current === "eyes_close" || current === "eyes_open" || goal === "happy")) {
                    token++;
                    busy = false;
                    current = null;
                    vs = "idle";
                }
                step();
            },
            begin: function () { step(); },
            readyToFly: function () { return true; },
            takeoff: 0,
            // Odoo is opened once the happy clip has had time to play
            leaveAfter: function () {
                var d = V.happy && !V.happy.isStill && V.happy.duration / (cfg("happy").speed || 1);
                if (run) {
                    // the whole run clip, then Odoo
                    return clamp((isFinite(d) && d > 0 ? d : 3) + 0.1, 1.2, 5);
                }
                return isFinite(d) && d > 0 ? clamp(d, 0.8, 2.8) : 1.5;
            },
            frame: function (dt, g) {
                // stills: blink by flashing the eyes-closed pose while idle
                if (V.idle && V.idle.isStill && V.eyes_close && V.eyes_close.isStill && !busy && vs === "idle" && goal === "idle") {
                    var now = performance.now();
                    if (!blinkAt) {
                        blinkAt = now + 2500 + Math.random() * 3000;
                    } else if (now > blinkAt && shown === V.idle) {
                        show(V.eyes_close);
                        blinkEnd = now + 140;
                        blinkAt = now + 2500 + Math.random() * 3500;
                    } else if (blinkEnd && now > blinkEnd && shown === V.eyes_close) {
                        blinkEnd = 0;
                        show(V.idle);
                    }
                }
                // login failure: the controller starts its head-shake timer
                if (g.shakeRot && performance.now() > sadUntil) {
                    sadUntil = performance.now() + 2000;
                    sad();
                }
            },
            toScreen: function (x, y) {
                var r = bird.getBoundingClientRect();
                return { x: r.left + x / W * r.width, y: r.top + y / H * r.height, s: r.width / W };
            },
            lift: function () { return 0; },
            onLost: noop,
            destroy: function () {
                Object.keys(V).forEach(function (n) { V[n].pause(); });
                box.remove();
            },
        };
        return api;
    }

    /* ============================================================
       3D character (Settings > Login Page > Animations, "3D model"):
       static/src/js/mascot3d.js + three.js are imported on demand;
       until the model is ready the stage just shows its background.
       ============================================================ */
    function Model3DRenderer(stage, bird, url) {
        var m = null;
        var pose = "idle";
        var dead = false;
        var api = {
            kind: "video",
            leaveAfterSec: 2.4,
            load: function () {
                var base = SCRIPT_SRC.replace(/login\.js(\?.*)?$/, "");
                return import(base + "mascot3d.js?v=" + ASSET_V).then(function (mod) {
                    return mod.createMascot3D(stage, url);
                }).then(function (mascot) {
                    if (dead) {
                        mascot.destroy();
                        return false;
                    }
                    m = mascot;
                    api.leaveAfterSec = m.leaveAfter;
                    m.setPose(pose);
                    stage.classList.add("is-ready");
                    return true;
                }).catch(function (e) {
                    if (window.console) {
                        console.warn("login 3D mascot:", e);
                    }
                    return false;
                });
            },
            has: function () { return true; },
            current: function () { return pose; },
            setPose: function (to) {
                pose = to;
                if (m) {
                    m.setPose(to);
                }
            },
            begin: noop,
            readyToFly: function () { return true; },
            takeoff: 0,
            leaveAfter: function () { return api.leaveAfterSec; },
            frame: function (dt, g) {
                if (m) {
                    m.input(g);
                }
            },
            toScreen: function (x, y) {
                if (m) {
                    return m.headScreen();
                }
                var r = bird.getBoundingClientRect();
                return { x: r.left + x / W * r.width, y: r.top + y / H * r.height, s: r.width / W };
            },
            lift: function () { return 0; },
            onLost: noop,
            destroy: function () {
                dead = true;
                if (m) {
                    m.destroy();
                }
            },
        };
        return api;
    }

    // resolves a ready VideoRenderer, or null (no clips yet, unsupported browser)
    function loadVideo(stage, bird) {
        if (!videoSupported() || !window.fetch) {
            return Promise.resolve(null);
        }
        return fetch(VIDEO_BASE + "clips.json?v=" + VIDEO_V, { credentials: "same-origin" }).then(function (res) {
            return res.ok ? res.json() : null;
        }).then(function (meta) {
            if (!meta || !meta.clips || !meta.clips.idle) {
                return null;
            }
            var R = VideoRenderer(stage, bird, meta);
            return R.load().then(function (ok) {
                if (ok) {
                    return R;
                }
                R.destroy();
                return null;
            });
        }).catch(function () { return null; });
    }

    function init() {
        var stage = document.querySelector(".pk-stage");
        if (!stage || stage.__pkInit) {
            return;
        }
        stage.__pkInit = true;

        var rig = stage.querySelector(".pk-stage__rig");
        var bird = stage.querySelector(".pk-stage__bird");
        var glow = stage.querySelector(".pk-stage__glow");
        var shadow = stage.querySelector(".pk-stage__shadow");
        var reduced = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
        var imgs = {};
        POSES.forEach(function (name) {
            imgs[name] = stage.querySelector('.pk-pose[data-pose="' + name + '"]');
        });
        var human = null;
        try {
            human = stage.getAttribute("data-clips") ? JSON.parse(stage.getAttribute("data-clips")) : null;
        } catch (e) {
            human = null;
        }
        var model3d = stage.getAttribute("data-model3d");
        if (model3d) {
            // the 3D mascot behaves like an uploaded one: no parrot flight on success
            human = human || {};
        }
        if (!bird || (!human && POSES.some(function (n) { return !imgs[n]; }))) {
            return;
        }

        var R;
        if (model3d) {
            R = Model3DRenderer(stage, bird, model3d);
            stage.classList.add("is-video");
        } else if (human) {
            R = HumanRenderer(stage, bird, human);
            stage.classList.add("is-video");
        } else {
            var flyImgs = {};
            Object.keys(FLY).forEach(function (n) {
                flyImgs[n] = stage.querySelector('.pk-fly[data-part="' + n + '"]');
            });
            R = GLRenderer(stage, bird, imgs, reduced, flyImgs);
            if (R) {
                stage.classList.add("is-gl");
            } else {
                R = ImageRenderer(stage, bird, imgs, reduced);
            }
        }
        var ready = false;
        var submitted = false;  // login accepted: success pose, then the flight
        var flying = false;
        var lockUntil = 0;      // keeps idle on screen while the error shake plays
        var shakeStart = -1;

        function mood() {
            if (submitted) {
                return "success";
            }
            if (performance.now() < lockUntil) {
                return "idle";
            }
            var pw = document.getElementById("pk_password");
            if (pw && pw.type === "text") {
                return "peek";
            }
            var a = document.activeElement;
            // the eye toggle sits inside the password field: still the password
            if (pw && (a === pw || (a && a.classList && a.classList.contains("pk-fld__eye")))) {
                return "shy";
            }
            if (a && (a.id === "login" || a.id === "totp_token")) {
                return "email";
            }
            return "idle";
        }

        function sync() {
            var want = mood();
            if (ready && want !== R.current() && R.has(want)) {
                R.setPose(want);
                start();
            }
        }

        function fallback() {
            var pose = R.current();
            R.destroy();
            stage.classList.remove("is-gl");
            R = ImageRenderer(stage, bird, imgs, reduced);
            return R.load().then(function () { R.setPose(pose); });
        }
        R.onLost(function () {
            // GPU context gone: carry on with the <img> stack
            fallback().then(start);
        });

        var timeout = new Promise(function (resolve) { setTimeout(resolve, 8000); });
        Promise.race([R.load(), timeout]).then(function (ok) {
            return stage.classList.contains("is-gl") && ok !== true ? fallback() : null;
        }).then(function () {
            ready = true;
            sync();
            start();
        });

        // upgrade to the pre-rendered video clips once they can play
        if (!reduced && !human) {
            loadVideo(stage, bird).then(function (vr) {
                if (!vr) {
                    return;
                }
                if (flying || submitted) {
                    vr.destroy();
                    return;
                }
                R.destroy();
                stage.classList.remove("is-gl");
                stage.classList.add("is-video");
                bird.style.transform = "";
                R = vr;
                R.setPose(mood());
                R.begin();
                start();
            });
        }

        /* ---------- where the bird looks ---------- */
        var mouse = null;
        var lastMove = -1e9;
        var lastType = -1e9;
        var measureSpan = null;
        var look = { x: spring(0, 60, 12), y: spring(0, 60, 12) };
        var eyes = { x: spring(0, 220, 22), y: spring(0, 220, 22) };
        var wander = { v: spring(0, 25, 8), x: 0, y: 0, next: 0 };
        var par = { x: spring(0, 40, 11), y: spring(0, 40, 11) };

        function caretPoint() {
            var login = document.getElementById("login");
            if (!login) {
                return null;
            }
            var r = login.getBoundingClientRect();
            if (!measureSpan) {
                measureSpan = document.createElement("span");
                measureSpan.style.cssText = "position:absolute;visibility:hidden;white-space:pre;left:-9999px;top:0";
                document.body.appendChild(measureSpan);
            }
            var cs = getComputedStyle(login);
            measureSpan.style.font = cs.font;
            measureSpan.style.letterSpacing = cs.letterSpacing;
            var pos = login.selectionEnd == null ? login.value.length : login.selectionEnd;
            measureSpan.textContent = login.value.slice(0, pos);
            var x = r.left + parseFloat(cs.paddingLeft || 0) + Math.min(measureSpan.offsetWidth - login.scrollLeft, r.width - 20);
            return { x: x, y: r.top + r.height / 2 };
        }

        function target(now) {
            var m = R.current();
            if (m === "email" && now - lastType < 2500) {
                return caretPoint() || mouse;
            }
            if (m === "peek") {
                var pw = document.getElementById("pk_password");
                if (pw) {
                    var r = pw.getBoundingClientRect();
                    return { x: r.left + Math.min(r.width * 0.3, 120), y: r.top + r.height / 2 };
                }
            }
            return now - lastMove < 4000 ? mouse : null;
        }

        /* ---------- loop ---------- */
        var raf = 0;
        var last = 0;
        var blinkStart = -1;
        var nextBlink = performance.now() + 1600;

        function frame(now) {
            raf = 0;
            if (document.hidden) {
                return;
            }
            var dt = last ? Math.min((now - last) / 1000, 0.05) : 1 / 60;
            last = now;
            sync();

            // look target → -1..1 relative to the head on screen
            var r = RIG[R.current()];
            var hc = R.toScreen(r.head[0], r.head[1]);
            var tp = target(now);
            if (tp) {
                var reach = 420 * Math.max(hc.s, 0.35);
                look.x.t = clamp((tp.x - hc.x) / reach, -1, 1);
                look.y.t = clamp((tp.y - hc.y) / reach, -1, 1);
                wander.v.t = 0;
            } else {
                // nobody is moving the mouse: glance around now and then
                look.x.t = look.y.t = 0;
                if (now > wander.next) {
                    wander.next = now + 1800 + Math.random() * 2600;
                    wander.v.t = Math.random() < 0.3 ? 0 : (Math.random() * 2 - 1) * 0.55;
                    wander.y = (Math.random() * 2 - 1) * 0.35;
                }
            }
            if (flying) {
                // looking where it is going: up and to the right
                look.x.t = 0.9;
                look.y.t = -0.7;
                wander.v.t = 0;
            }
            eyes.x.t = clamp(look.x.t + wander.v.t * 0.8, -1, 1);
            eyes.y.t = tp ? look.y.t : wander.y * 0.5;
            stepAll([look.x, look.y, eyes.x, eyes.y, wander.v], dt);

            // blink every few seconds, sometimes twice
            if (now > nextBlink && blinkStart < 0) {
                blinkStart = now;
                nextBlink = now + (Math.random() < 0.2 ? 320 : 2400 + Math.random() * 3200);
            }
            var blink = 0;
            if (blinkStart >= 0) {
                var bt = (now - blinkStart) / 1000;
                if (bt > 0.18) {
                    blinkStart = -1;
                } else {
                    blink = bt < 0.07 ? bt / 0.07 : 1 - (bt - 0.07) / 0.11;
                }
            }

            var s = now / 1000;
            var breath = 0.5 - 0.5 * Math.cos(s * Math.PI * 2 / 3.6);
            var sway = Math.sin(s * Math.PI * 2 / 7.4) * 0.8 + Math.sin(s * Math.PI * 2 / 3.3 + 1.3) * 0.25;
            var shake = 0;
            if (shakeStart >= 0 && now >= shakeStart) {
                var k = (now - shakeStart) / 1000;
                if (k < 1.2) {
                    shake = Math.sin(k * Math.PI * 2 * 3.6) * Math.exp(-k * 3);
                } else {
                    shakeStart = -1;
                }
            }

            var fly = flying && fl ? flightFrame(now) : null;
            if (!(fl && fl.birdGone)) {
                // (once the sprite has taken over, the photo bird is hidden: no need to draw it)
                R.frame(dt, {
                    fly: fly,
                    lookX: look.x.v, lookY: look.y.v,
                    eyeX: eyes.x.v, eyeY: eyes.y.v,
                    wander: wander.v.v,
                    blink: clamp(blink, 0, 1),
                    breath: breath,
                    lean: sway * 0.012 + look.x.v * 0.012,
                    bob: Math.sin(s * 1.7) * 0.012,
                    shakeYaw: shake * 0.7, shakeRot: shake * 0.1,
                    flap: flying ? Math.sin(s * 34) * 0.5 : R.current() === "success" ? Math.sin(s * 11) * 0.16 : 0,
                });
            }

            // whole stage: soft parallax; background light and floor shadow follow
            par.x.t = mouse && now - lastMove < 4000 ? clamp((mouse.x - hc.x) / (window.innerWidth * 0.5), -1, 1) : 0;
            par.y.t = mouse && now - lastMove < 4000 ? clamp((mouse.y - hc.y) / (window.innerHeight * 0.5), -1, 1) : 0;
            stepAll([par.x, par.y], dt);
            if (rig) {
                rig.style.transform = "translate3d(" + (par.x.v * 5).toFixed(2) + "px," + (par.y.v * 3).toFixed(2) +
                    "px,0) rotateY(" + (par.x.v * 3).toFixed(3) + "deg) rotateX(" + (-par.y.v * 2).toFixed(3) + "deg)";
            }
            if (glow) {
                glow.style.transform = "translate(" + (-par.x.v * 12).toFixed(2) + "px," + (-par.y.v * 8).toFixed(2) + "px)";
            }
            if (shadow) {
                var lift = R.lift();
                var sc = (1 + Math.abs(sway) * 0.03 + breath * 0.02) * (1 - lift * 0.25);
                shadow.style.transform = "translateX(" + (par.x.v * 5 + sway * 3).toFixed(2) + "px) scale(" +
                    sc.toFixed(4) + "," + (1 - lift * 0.2).toFixed(4) + ")";
                shadow.style.opacity = (1 - lift * 0.4).toFixed(3);
            }

            raf = requestAnimationFrame(frame);
        }

        function start() {
            if (!raf && !document.hidden) {
                last = 0;
                raf = requestAnimationFrame(frame);
            }
        }

        /* ---------- events (delegated: fields may not exist on every page) ---------- */
        function later() { setTimeout(sync, 0); }
        document.addEventListener("focusin", later);
        document.addEventListener("focusout", later);
        // the eye button's inline onclick has already flipped the input type
        document.addEventListener("click", later);
        document.addEventListener("input", function (e) {
            if (e.target && e.target.id === "login") {
                lastType = performance.now();
            }
        });
        /* ---------- login: post in the background, fly away on success ----------
           The form is posted with fetch() and the "X-Perok-Login" header; for a
           successful login the server (models/ir_http.py) answers with the
           redirect target as JSON instead of redirecting, after every controller
           override has run (2FA page, "/web#action=..." targets from other
           modules, debug "/web/become"). A failed login answers with the login
           page as usual: its error is shown in place. */
        var busy = false;

        function button(form, on) {
            var b = form.querySelector(".pk-btn--dark") || form.querySelector('button[type="submit"]');
            if (b) {
                b.classList.toggle("is-loading", on);
                b.disabled = on;
            }
        }

        function showError(form, doc, fallback) {
            var src = doc && (doc.querySelector(".pk-alert--err span") || doc.querySelector(".alert-danger"));
            var msg = src ? src.textContent.trim() : "";
            var token = doc && doc.querySelector('input[name="csrf_token"]');
            var mine = form.querySelector('input[name="csrf_token"]');
            if (token && mine) {
                mine.value = token.value;
            }
            var alert = form.querySelector(".pk-alert--err");
            var code = form.querySelector('input[name="totp_token"]');
            if (!alert && code) {
                // 2FA step: an alert above the code field
                var old = form.querySelector(".alert-danger");
                if (old) {
                    old.remove();
                }
                alert = document.createElement("div");
                alert.className = "pk-alert pk-alert--err";
                alert.setAttribute("role", "alert");
                alert.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                    '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg><span></span>';
                code.parentNode.insertBefore(alert, code.parentNode.firstChild);
            }
            if (code) {
                code.value = "";
                code.focus();
            }
            if (!alert) {
                var tpl = document.createElement("div");
                tpl.innerHTML = '<div class="pk-alert pk-alert--err" role="alert">' +
                    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                    '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>' +
                    "<span></span></div>";
                alert = tpl.firstChild;
                form.insertBefore(alert, form.querySelector(".pk-fld"));
            }
            alert.querySelector("span").textContent = msg || fallback || "Wrong login/password";
            var ok = form.querySelector(".pk-alert--ok");
            if (ok) {
                ok.remove();
            }
            var pw = document.getElementById("pk_password");
            if (pw) {
                pw.value = "";
                if (pw.type === "text") {
                    var eye = form.querySelector(".pk-fld__eye");
                    if (eye) {
                        eye.click();    // its inline handler also swaps the icon back
                    }
                }
                pw.focus();
            }
            var now = performance.now();
            lockUntil = now + 1400;
            shakeStart = now + 120;
            sync();
        }

        /* ---------- the flight ----------
           Crouch on the front-facing success pose with the wings raised, then:
           - sprite (flight.webp / flight.json, preloaded after the page has
             painted): the photo bird turns away (narrows, fades) while the
             side-view sprite turns in, plays its take-off frames once, then
             loops the wing-beat cycle; nose up while climbing, the body
             rising on every downstroke.
           - fallback (no sprite): the GL wing layers flap (fast downstroke,
             slower half-folded upstroke, tips trailing the shoulders); with
             the <img> renderer the whole bird just follows the path.
           Either way the path climbs first, then sweeps off the top-right
           corner, shrinking into the distance; Odoo starts loading while the
           bird is still on its way out. */
        var FLIGHT = { crouch: 0.22, beats: 6, travel: 1.7, leave: 1.3, angle: 30 };
        var WING_UP = 0.95, WING_DOWN = -0.55, DOWNSTROKE = 0.42;
        // sprite flight: cross-fade (s), max nose-up (deg), bob (share of bodyWidth)
        var SPRITE = { fade: 0.14, pitch: 12, bob: 0.05 };
        var fl = null;
        var sprite = null;      // loaded flight sprite, null until ready (or for good when missing)
        var spriteLoad = null;

        /* flying pose: the same bird drawn in flight (img/parrot/pose_fly_*),
           cut into body + two wings that beat around the shoulders */
        var POSE_DIR = "/perok_login/static/src/img/parrot/";
        var POSE_FLY_ENABLED = false;   // off: the success pose's own wings beat instead
        var flyPose = null;
        var poseLoad = null;
        function preloadPose() {
            if (!POSE_FLY_ENABLED) {
                return Promise.resolve(null);
            }
            if (!poseLoad) {
                poseLoad = fetch(POSE_DIR + "pose_fly.json?v=" + ASSET_V, { credentials: "same-origin" })
                    .then(function (res) { return res.ok ? res.json() : null; })
                    .then(function (m) {
                        if (!m || !(m.w > 0) || !m.pivotL || !m.pivotR || !m.head) {
                            return null;
                        }
                        var img = {};
                        ["body", "l", "r"].forEach(function (n) {
                            img[n] = new Image();
                            img[n].src = POSE_DIR + "pose_fly_" + n + ".webp?v=" + ASSET_V;
                        });
                        return Promise.all(["body", "l", "r"].map(function (n) { return decode(img[n]); }))
                            .then(function () {
                                flyPose = { m: m, img: img };
                                return flyPose;
                            });
                    }).catch(function () { return null; });
            }
            return poseLoad;
        }

        function preloadSprite() {
            preloadPose();
            if (!spriteLoad) {
                spriteLoad = loadSprite().then(function (sp) {
                    sprite = sp;
                    return sp;
                });
            }
            return spriteLoad;
        }
        if (!reduced) {
            // after the page has painted, when the browser is idle: never delays it
            var whenIdle = function () {
                if (window.requestIdleCallback) {
                    window.requestIdleCallback(preloadSprite, { timeout: 2500 });
                } else {
                    setTimeout(preloadSprite, 600);
                }
            };
            if (document.readyState === "complete") {
                whenIdle();
            } else {
                window.addEventListener("load", whenIdle, { once: true });
            }
        }

        function smooth01(v) {
            v = clamp(v, 0, 1);
            return v * v * (3 - 2 * v);
        }
        function wingAt(ph) {
            ph -= Math.floor(ph);
            return ph < DOWNSTROKE
                ? WING_UP + (WING_DOWN - WING_UP) * smooth01(ph / DOWNSTROKE)
                : WING_DOWN + (WING_UP - WING_DOWN) * smooth01((ph - DOWNSTROKE) / (1 - DOWNSTROKE));
        }

        // GL wing layers crouching with the wings raised (k: 0..1)
        function crouchPose(k) {
            return {
                base: WING_UP * k, tip: WING_UP * 1.12 * k, fold: 1, tilt: 0,
                sx: 1 + 0.04 * k, sy: 1 - 0.1 * k, ty: 18 * k,
            };
        }

        // quadratic curve: up first, then across to the corner (e: 0..1)
        // straight across the screen, climbing at FLIGHT.angle degrees
        function pathAt(e) {
            var a = FLIGHT.angle * Math.PI / 180;
            return { x: e * fl.dist * Math.cos(a), y: -e * fl.dist * Math.sin(a), dir: -FLIGHT.angle };
        }
        function travelAt(tf) { var k = Math.min(1, tf / FLIGHT.travel); return k * k * (2 - k) * 0.5 + k * 0.5; }
        function distanceScale() { return 1; }

        function leaveAt(tf) {
            if (!fl.went && tf >= FLIGHT.leave) {
                // start loading Odoo while the bird is still on its way out
                fl.went = true;
                clearTimeout(fl.guard);
                window.location.assign(fl.href);
            }
        }

        function flightFrame(now) {
            var t = (now - fl.start) / 1000;
            if (fl.human) {
                leaveAt(t);
                return { human: true };
            }
            if (fl.sprite) {
                return spriteFrame(t);
            }
            if (fl.pose) {
                return poseFrame(t);
            }
            var f;
            var e = 0, ph = 0;
            if (t < FLIGHT.crouch) {
                f = crouchPose(smooth01(t / FLIGHT.crouch));
            } else {
                f = { base: 0, tip: 0, fold: 1, tilt: 0, sx: 1, sy: 1, ty: 0 };
                var tf = t - FLIGHT.crouch;
                ph = tf * FLIGHT.beats;
                var cyc = ph - Math.floor(ph);
                f.base = wingAt(ph);
                f.tip = wingAt(ph - 0.07) * 1.12;
                var up = cyc >= DOWNSTROKE ? Math.sin(Math.PI * (cyc - DOWNSTROKE) / (1 - DOWNSTROKE)) : 0;
                var push = cyc < DOWNSTROKE ? Math.sin(Math.PI * cyc / DOWNSTROKE) : 0;
                var rel = Math.max(0, 1 - tf / 0.12);       // springing out of the crouch
                // upstroke: wings half folded; downstroke: full span, body heaved up
                f.fold = 1 - 0.45 * up;
                f.ty = -8 * push + 4 * up + 18 * rel;
                f.sx = 1 - 0.02 * push + 0.04 * rel;
                f.sy = 1 + 0.04 * push - 0.03 * up - 0.1 * rel;
                f.tuck = 0;     // standing: the feet stay down
                e = travelAt(tf);
                leaveAt(tf);
            }
            var p = pathAt(e);
            var bank = t < FLIGHT.crouch ? -4 * smooth01(t / FLIGHT.crouch)
                : 3 * Math.sin(Math.PI * 2 * ph);
            bird.style.transform = "translate(" + p.x.toFixed(1) + "px," + p.y.toFixed(1) + "px) rotate(" +
                bank.toFixed(2) + "deg) scale(" + distanceScale(e).toFixed(4) + ")";
            return f;
        }

        // sprite sequence: the take-off frames once, then the wing-beat loop
        function seqFrame(sp, i) {
            var n = sp.takeoff.length;
            return i < n ? sp.takeoff[i] : sp.loop[(i - n) % sp.loop.length];
        }

        function spriteFrame(t) {
            var fy = fl.sprite, sp = fy.sp, el = fy.el;
            if (t < FLIGHT.crouch) {
                // still the photo bird: crouch, wings up
                return crouchPose(smooth01(t / FLIGHT.crouch));
            }
            var tf = t - FLIGHT.crouch;
            var e = travelAt(tf);
            var p = pathAt(e);
            var sc = distanceScale(e);
            leaveAt(tf);

            // frame: time based, at the sprite's own fps
            var pos = tf * sp.fps;
            var i = Math.floor(pos), frac = pos - i;
            var cur = seqFrame(sp, i), nxt = seqFrame(sp, i + 1);
            if (cur !== fy.frame) {
                fy.frame = cur;
                el.style.backgroundPosition = -((cur % sp.cols) * sp.fw) + "px " + -(Math.floor(cur / sp.cols) * sp.fh) + "px";
            }
            // wing height (1 = up): measured per frame, else a cosine over the loop
            var up;
            if (sp.up) {
                up = sp.up[cur] + (sp.up[nxt] - sp.up[cur]) * frac;
            } else {
                var lp = (pos - sp.takeoff.length) / sp.loop.length;
                up = 0.5 + 0.5 * Math.cos(Math.PI * 2 * (lp - Math.floor(lp)));
            }
            // body rides low with the wings up and is lifted by the downstroke
            var bob = sp.bodyWidth * SPRITE.bob * (up - 0.5);
            // nose up while climbing, levelling out as it sweeps right
            var pitch = clamp(p.dir * 0.42, -SPRITE.pitch, -3) * smooth01(tf / 0.35) + 3 * (up - 0.5);

            // the turn: photo bird narrows and fades, the side view widens in
            var u = smooth01(tf / SPRITE.fade);
            var s = fy.scale * sc;
            el.style.opacity = u.toFixed(3);
            el.style.transform = "translate(" + p.x.toFixed(1) + "px," + p.y.toFixed(1) + "px) rotate(" +
                (pitch + 10 * (1 - u)).toFixed(2) + "deg) scale(" + (s * (0.55 + 0.45 * u)).toFixed(4) + "," +
                (s * (1.06 - 0.06 * u)).toFixed(4) + ") translateY(" + bob.toFixed(2) + "px)";
            if (u < 1) {
                bird.style.opacity = (1 - u).toFixed(3);
                bird.style.transform = "translate(" + p.x.toFixed(1) + "px," + p.y.toFixed(1) + "px) rotate(" +
                    (-6 * u).toFixed(2) + "deg) scale(" + (sc * (1 - 0.45 * u)).toFixed(4) + "," +
                    (sc * (1 + 0.03 * u)).toFixed(4) + ")";
            } else if (!fl.birdGone) {
                fl.birdGone = true;
                bird.style.visibility = "hidden";
            }
            if (e >= 1) {
                fl.layer.style.visibility = "hidden";
            }
            return crouchPose(1);
        }

        // flying pose element: same head position and size as the standing bird
        function makePoseFlyer(fp, layer, r) {
            var m = fp.m, hs = RIG.success.head;
            // a touch smaller than a head-for-head match: the open wings stay mostly off the form
            var s = (hs[2] * 2 * 0.95 / W * r.width) / m.headWidth * 0.82;
            var hx = r.left + hs[0] / W * r.width, hy = r.top + hs[1] / H * r.height;
            var el = document.createElement("div");
            el.className = "pk-poseflyer";
            el.setAttribute("aria-hidden", "true");
            el.style.left = (hx - m.head[0] * s).toFixed(1) + "px";
            el.style.top = (hy - m.head[1] * s).toFixed(1) + "px";
            el.style.width = (m.w * s).toFixed(1) + "px";
            el.style.height = (m.h * s).toFixed(1) + "px";
            el.style.transformOrigin = (m.head[0] * s).toFixed(1) + "px " + (m.head[1] * s).toFixed(1) + "px";
            el.style.opacity = "0";
            var parts = {};
            // wings first: the body covers their roots
            [["l", m.pivotL], ["r", m.pivotR], ["body", null]].forEach(function (d) {
                var im = fp.img[d[0]].cloneNode();
                im.alt = "";
                im.draggable = false;
                if (d[1]) {
                    im.style.transformOrigin = (d[1][0] / m.w * 100) + "% " + (d[1][1] / m.h * 100) + "%";
                }
                el.appendChild(im);
                parts[d[0]] = im;
            });
            layer.appendChild(el);
            return { el: el, parts: parts };
        }

        // 0 = wings up, 1 = wings down: quick downstroke, slower recovery
        function strokeAt(ph) {
            ph -= Math.floor(ph);
            return ph < 0.42 ? smooth01(ph / 0.42) : 1 - smooth01((ph - 0.42) / 0.58);
        }

        function poseFrame(t) {
            var pz = fl.pose;
            if (t < FLIGHT.crouch) {
                // still the standing bird: crouch with the wings raised
                return crouchPose(smooth01(t / FLIGHT.crouch));
            }
            var tf = t - FLIGHT.crouch;
            var e = travelAt(tf);
            var p = pathAt(e);
            var sc = distanceScale(e);
            leaveAt(tf);

            // the turn into the flying pose: quick cross-fade with a little spring
            var u = smooth01(tf / 0.16);
            var pop = 1 + 0.06 * Math.sin(Math.PI * smooth01(tf / 0.3));
            var ph = tf * FLIGHT.beats;
            var w = strokeAt(ph);
            var push = Math.sin(Math.PI * Math.min(1, (ph - Math.floor(ph)) / 0.42));
            // CSS: positive = clockwise; the left wing comes down anticlockwise
            pz.parts.l.style.transform = "rotate(" + (14 - 64 * w).toFixed(2) + "deg) scale(" + (1 - 0.06 * w).toFixed(3) + ")";
            pz.parts.r.style.transform = "rotate(" + (-24 + 66 * w).toFixed(2) + "deg) scale(" + (1 - 0.08 * w).toFixed(3) + ")";
            var bob = -8 * push;      // every downstroke lifts the body a little
            var tilt = -4 + 3 * Math.sin(Math.PI * 2 * ph);
            pz.el.style.opacity = u.toFixed(3);
            pz.el.style.transform = "translate(" + p.x.toFixed(1) + "px," + (p.y + bob).toFixed(1) + "px) rotate(" +
                tilt.toFixed(2) + "deg) scale(" + (sc * pop * (0.92 + 0.08 * u)).toFixed(4) + ")";
            if (u < 1) {
                bird.style.opacity = (1 - u).toFixed(3);
                bird.style.transform = "translate(" + p.x.toFixed(1) + "px," + p.y.toFixed(1) + "px) scale(" +
                    (sc * (1 + 0.05 * u)).toFixed(4) + ")";
            } else if (!fl.birdGone) {
                fl.birdGone = true;
                bird.style.visibility = "hidden";
            }
            return crouchPose(1);
        }

        // the side-view sprite, anchored on the photo bird's body
        function makeFlyer(sp, layer, r) {
            var el = document.createElement("div");
            el.className = "pk-flyer";
            el.setAttribute("aria-hidden", "true");
            var bx = r.left + BODY.x / W * r.width, by = r.top + BODY.y / H * r.height;
            el.style.width = sp.fw + "px";
            el.style.height = sp.fh + "px";
            el.style.left = (bx - sp.anchor[0]).toFixed(1) + "px";
            el.style.top = (by - sp.anchor[1]).toFixed(1) + "px";
            el.style.transformOrigin = sp.anchor[0] + "px " + sp.anchor[1] + "px";
            el.style.backgroundImage = 'url("' + sp.url + '")';
            el.style.backgroundSize = sp.aw + "px " + sp.ah + "px";
            el.style.backgroundPosition = "0 0";
            layer.appendChild(el);
            return {
                el: el, sp: sp, frame: -1,
                // body as long as the photo bird's body is wide (times the side/front ratio)
                scale: clamp(BODY.w * SIDE_LENGTH * (r.width / W) / sp.bodyWidth, 0.1, 4),
            };
        }

        function flyAway(href) {
            submitted = true;
            lockUntil = 0;
            sync();
            start();
            if (reduced) {
                window.location.assign(href);
                return;
            }
            preloadSprite();    // in case the idle preload has not run yet
            var guard = setTimeout(function () { window.location.assign(href); }, 3600);
            var video = R.kind === "video";
            function takeOff() {
                if (human) {
                    // the happy clip plays where it is; Odoo opens after it
                    fl = { start: performance.now(), href: href, guard: guard, went: false, human: true };
                    flying = true;
                    start();
                    return;
                }
                // lift the bird out of the card into a fixed layer above the page
                var r = bird.getBoundingClientRect();
                var layer = document.createElement("div");
                layer.className = "pk-flight";
                document.body.appendChild(layer);
                bird.style.left = r.left + "px";
                bird.style.top = r.top + "px";
                bird.style.width = r.width + "px";
                bird.style.height = r.height + "px";
                bird.style.bottom = "auto";
                bird.style.translate = "none";
                // out of the stage the ".pk-stage.is-gl" rule no longer hides the <img> poses
                bird.classList.toggle("is-gl", stage.classList.contains("is-gl"));
                layer.appendChild(bird);
                var fy = null;
                if (sprite && !video) {
                    try {
                        fy = makeFlyer(sprite, layer, r);
                    } catch (e) {
                        fy = null;
                    }
                }
                var pz = null;
                if (!fy && flyPose && !video) {
                    try {
                        pz = makePoseFlyer(flyPose, layer, r);
                    } catch (e) {
                        pz = null;
                    }
                }
                // sprite: scale around the body so the photo bird and the sprite stay on top of each other
                bird.style.transformOrigin = pz
                    ? (RIG.success.head[0] / W * 100) + "% " + (RIG.success.head[1] / H * 100) + "%"
                    : fy
                    ? (BODY.x / W * 100) + "% " + (BODY.y / H * 100) + "%"
                    : "50% 55%";
                if (shadow && shadow.animate) {
                    shadow.animate([{ opacity: 1 }, { opacity: 0, transform: "scale(0.4)" }],
                        { duration: 450, delay: 150, easing: "ease-out", fill: "forwards" });
                }
                fl = {
                    start: performance.now(), href: href, layer: layer, guard: guard, went: false,
                    sprite: fy, pose: pz, birdGone: false,
                    // off the top-right corner of the window
                    // far enough along the 30 degree line to leave the window on the right
                    // the bird stays where it is and only flaps (no flight across the page)
                    dist: 0,
                };
                flying = true;
                start();
            }
            if (video) {
                // the fly clip starts from the neutral pose: let a look/cover clip finish first
                FLIGHT.crouch = R.takeoff;
                if (R.leaveAfter) {
                    FLIGHT.leave = R.leaveAfter();
                }
                var t0 = performance.now();
                (function wait() {
                    if (R.readyToFly() || performance.now() - t0 > 1500) {
                        takeOff();
                    } else {
                        setTimeout(wait, 50);
                    }
                })();
            } else {
                setTimeout(takeOff, 450);
            }
        }

        // the form's fields, with the clicked button's name/value where the
        // browser would put it (e.g. debug "Log in as superuser")
        function formData(form, submitter) {
            try {
                return new FormData(form, submitter || undefined);
            } catch (e) {
                var fd = new FormData(form);
                if (submitter && submitter.name) {
                    var rest = fd.getAll(submitter.name);
                    fd.delete(submitter.name);
                    fd.append(submitter.name, submitter.value);
                    rest.forEach(function (v) { fd.append(submitter.name, v); });
                }
                return fd;
            }
        }

        // reCAPTCHA v3 token for the login when the site has a key (the
        // google_recaptcha interaction loads grecaptcha); none otherwise
        function captchaToken(form) {
            var info = window.odoo && window.odoo.__session_info__;
            var key = info && info.recaptcha_public_key;
            if (!form.dataset.captcha || !key || !window.grecaptcha || !window.grecaptcha.execute) {
                return Promise.resolve(null);
            }
            return new Promise(function (resolve) {
                window.grecaptcha.ready(function () {
                    window.grecaptcha.execute(key, { action: form.dataset.captcha }).then(resolve, function () { resolve(null); });
                });
            });
        }

        function postInBackground(form, submitter) {
            busy = true;
            button(form, true);
            function done() {
                busy = false;
                button(form, false);
            }
            captchaToken(form).then(function (token) {
                var body = formData(form, submitter);
                if (token) {
                    body.set("recaptcha_token_response", token);
                }
                return fetch(form.action || "/web/login", {
                    method: "POST",
                    body: body,
                    credentials: "same-origin",
                    redirect: "manual",
                    headers: { "X-Perok-Login": "1" },
                });
            }).then(function (res) {
                if (res.type === "opaqueredirect") {
                    // logged in, but the redirect was not rewritten (module not
                    // upgraded yet): go where Odoo goes by default
                    var r = form.querySelector('input[name="redirect"]');
                    return { redirect: (r && r.value) || "/odoo" };
                }
                var type = res.headers.get("Content-Type") || "";
                if (type.indexOf("application/json") >= 0) {
                    return res.json();
                }
                return res.text().then(function (html) {
                    return { html: html, status: res.status };
                });
            }).then(function (out) {
                if (out.redirect) {
                    if (new URL(out.redirect, window.location.href).pathname.indexOf("/web/login") === 0) {
                        window.location.assign(out.redirect);    // 2FA step: not logged in yet
                    } else {
                        flyAway(out.redirect);
                    }
                    return;
                }
                done();
                var doc = new DOMParser().parseFromString(out.html || "", "text/html");
                if (doc.querySelector(".oe_login_form")) {
                    showError(form, doc, isTotp(form) ? "Invalid authentication code" : null);
                } else {
                    // not the login page (e.g. an expired session): no message to reuse
                    showError(form, null, "Something went wrong (" + out.status + "). Please reload the page and try again.");
                }
            }).catch(function () {
                done();
                showError(form, null, "Could not reach the server. Please try again.");
            });
        }

        // capture phase: runs before Odoo's own submit handler, which then sees
        // the event as handled and skips its button spinner
        // the 2FA step (/web/login/totp): its code form is posted the same way
        function isTotp(form) {
            return !!form.querySelector('input[name="totp_token"]');
        }
        document.addEventListener("submit", function (e) {
            var form = e.target;
            if (!form || !form.closest || !form.closest(".pk-form-box") ||
                    !(form.classList.contains("pk-form") || isTotp(form))) {
                return;
            }
            e.preventDefault();
            // Odoo 19's reCAPTCHA interaction submits the form natively whatever
            // the event says: keep the event away from the form's own handlers
            e.stopPropagation();
            if (busy || submitted) {
                return;
            }
            var submitter = e.submitter;
            // let the inline onsubmit (sets the action) run first
            setTimeout(function () { postInBackground(form, submitter); }, 0);
        }, true);
        window.addEventListener("pageshow", function (e) {
            if (e.persisted) {
                // back from Odoo via the bfcache: put the bird back
                window.location.reload();
            }
        });
        function release() { lockUntil = 0; }
        document.addEventListener("keydown", release);
        document.addEventListener("pointerdown", release);
        document.addEventListener("pointermove", function (e) {
            if (e.pointerType !== "touch") {
                mouse = { x: e.clientX, y: e.clientY };
                lastMove = performance.now();
            }
        }, { passive: true });
        document.addEventListener("mouseout", function (e) {
            if (!e.relatedTarget) {
                mouse = null;
            }
        });
        document.addEventListener("visibilitychange", start);

        // login error: hold the idle pose and shake its head once the bird is in
        if (document.querySelector(".pk-alert--err")) {
            lockUntil = performance.now() + 2200;
            shakeStart = performance.now() + 900;
        }
        if (reduced) {
            // no idle motion: draw once per change only
            var still = function () {
                R.frame(0, { lookX: 0, lookY: 0, eyeX: 0, eyeY: 0, wander: 0, blink: 0, breath: 0, lean: 0,
                    bob: 0, shakeYaw: 0, shakeRot: 0, flap: 0 });
            };
            start = function () { sync(); requestAnimationFrame(still); };
        }
        start();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
