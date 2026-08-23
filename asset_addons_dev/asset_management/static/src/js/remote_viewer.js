/* Remote Assistance — admin browser viewer
 * Connects to the relay as a "viewer", renders JPEG frames the host streams,
 * and forwards mouse/keyboard events (normalized 0..1) back to the host.
 */
(function () {
  "use strict";

  var body = document.body;
  var TOKEN = body.dataset.token;
  var RELAY = body.dataset.relay;
  var CUSTOMER = body.dataset.customer || "customer";

  var canvas = document.getElementById("ra-canvas");
  var ctx = canvas.getContext("2d");
  var stage = document.getElementById("ra-stage");
  var overlay = document.getElementById("ra-overlay");
  var overlayText = document.getElementById("ra-overlay-text");
  var statusEl = document.getElementById("ra-status");
  var dotEl = document.getElementById("ra-dot");
  var timerEl = document.getElementById("ra-timer");
  var qualityEl = document.getElementById("ra-quality");

  var ws = null;
  var connected = false;
  var startTs = null;
  var frameCount = 0;
  var lastFpsTs = Date.now();
  // Natural size of the incoming frames (image intrinsic size).
  var frameW = 0, frameH = 0;
  // Rectangle the frame is drawn into inside the canvas (letterboxed).
  var draw = { x: 0, y: 0, w: 0, h: 0 };
  var img = new Image();
  var pendingUrl = null;

  // ---- connection ------------------------------------------------------
  function setStatus(text, state) {
    statusEl.textContent = text;
    dotEl.className = "ra-dot" + (state ? " ra-" + state : "");
  }

  function connect() {
    if (!RELAY) {
      setStatus("no relay configured", "bad");
      overlayText.textContent =
        "Relay URL is not set. Configure remote_assist.relay_url in Odoo.";
      return;
    }
    setStatus("connecting…", "warn");
    try {
      ws = new WebSocket(RELAY);
    } catch (e) {
      setStatus("connection failed", "bad");
      return;
    }
    ws.binaryType = "arraybuffer";

    ws.onopen = function () {
      ws.send(JSON.stringify({ type: "hello", role: "viewer", token: TOKEN }));
    };

    ws.onmessage = function (ev) {
      if (typeof ev.data === "string") {
        handleControl(ev.data);
      } else {
        renderFrame(ev.data);
      }
    };

    ws.onclose = function () {
      connected = false;
      setStatus("disconnected", "bad");
      overlay.style.display = "grid";
      overlayText.textContent = "The session has ended.";
    };
    ws.onerror = function () { setStatus("connection error", "bad"); };
  }

  function handleControl(text) {
    var obj;
    try { obj = JSON.parse(text); } catch (e) { return; }
    if (obj.type === "welcome") {
      setStatus("waiting for screen…", "warn");
    } else if (obj.type === "error") {
      setStatus(obj.reason || "rejected", "bad");
      overlayText.textContent = "Relay rejected the session: " +
        (obj.reason || "unknown");
    } else if (obj.type === "host_info") {
      document.getElementById("ra-title").textContent =
        (obj.os ? obj.os + " • " : "") + CUSTOMER;
    } else if (obj.type === "peer" && obj.event === "leave" &&
               obj.role === "host") {
      setStatus("customer stopped sharing", "bad");
      overlay.style.display = "grid";
      overlayText.textContent = "The customer stopped sharing.";
    }
  }

  // ---- rendering -------------------------------------------------------
  function renderFrame(arrayBuffer) {
    if (!connected) {
      connected = true;
      startTs = Date.now();
      overlay.style.display = "none";
      setStatus("connected", "ok");
    }
    var blob = new Blob([arrayBuffer], { type: "image/jpeg" });
    var url = URL.createObjectURL(blob);
    // Revoke the previous URL once the next has loaded to bound memory.
    var prev = pendingUrl;
    pendingUrl = url;
    img.onload = function () {
      frameW = img.naturalWidth;
      frameH = img.naturalHeight;
      layout();
      ctx.drawImage(img, draw.x, draw.y, draw.w, draw.h);
      if (prev) URL.revokeObjectURL(prev);
      frameCount++;
    };
    img.src = url;
  }

  function layout() {
    var rect = stage.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
    if (!frameW || !frameH) return;
    var scale = Math.min(rect.width / frameW, rect.height / frameH);
    draw.w = frameW * scale;
    draw.h = frameH * scale;
    draw.x = (rect.width - draw.w) / 2;
    draw.y = (rect.height - draw.h) / 2;
    // Repaint background bars.
    ctx.fillStyle = "#05070d";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
  window.addEventListener("resize", layout);

  // ---- input capture ---------------------------------------------------
  function norm(ev) {
    // Map a browser event to normalized coords within the drawn frame.
    var rect = canvas.getBoundingClientRect();
    var px = ev.clientX - rect.left - draw.x;
    var py = ev.clientY - rect.top - draw.y;
    if (draw.w <= 0 || draw.h <= 0) return null;
    var nx = px / draw.w, ny = py / draw.h;
    if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return null;
    return { x: nx, y: ny };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  var BUTTONS = { 0: "left", 1: "middle", 2: "right" };
  var lastMoveSent = 0;

  canvas.addEventListener("mousemove", function (ev) {
    var now = performance.now();
    if (now - lastMoveSent < 30) return;         // throttle to ~33/s
    lastMoveSent = now;
    var p = norm(ev);
    if (p) send({ type: "mouse_move", x: p.x, y: p.y });
  });
  canvas.addEventListener("mousedown", function (ev) {
    var p = norm(ev);
    if (p) send({ type: "mouse_down", x: p.x, y: p.y,
                  button: BUTTONS[ev.button] || "left" });
  });
  canvas.addEventListener("mouseup", function (ev) {
    var p = norm(ev);
    if (p) send({ type: "mouse_up", x: p.x, y: p.y,
                  button: BUTTONS[ev.button] || "left" });
  });
  canvas.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });
  canvas.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    send({ type: "scroll", dy: ev.deltaY > 0 ? -1 : 1 });
  }, { passive: false });

  document.addEventListener("keydown", function (ev) {
    if (!connected) return;
    ev.preventDefault();
    send({ type: "key_down", key: ev.key });
  });
  document.addEventListener("keyup", function (ev) {
    if (!connected) return;
    ev.preventDefault();
    send({ type: "key_up", key: ev.key });
  });

  // ---- controls & meters ----------------------------------------------
  document.getElementById("ra-end").addEventListener("click", function () {
    send({ type: "end_session" });
    if (ws) ws.close();
    setStatus("session ended", "bad");
    overlay.style.display = "grid";
    overlayText.textContent = "You ended the session.";
  });
  document.getElementById("ra-fit").addEventListener("click", layout);

  setInterval(function () {
    if (startTs) {
      var s = Math.floor((Date.now() - startTs) / 1000);
      timerEl.textContent =
        String(Math.floor(s / 60)).padStart(2, "0") + ":" +
        String(s % 60).padStart(2, "0");
    }
    var now = Date.now();
    var fps = frameCount / ((now - lastFpsTs) / 1000);
    qualityEl.textContent = (isFinite(fps) ? fps.toFixed(0) : "–") + " fps";
    frameCount = 0;
    lastFpsTs = now;
  }, 1000);

  // keepalive
  setInterval(function () { send({ type: "ping" }); }, 20000);

  connect();
})();
