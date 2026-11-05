/* SSH Terminal viewer — admin browser side.
 *
 * Same relay connection pattern as remote_viewer.js (outbound-only
 * WebSocket to the relay, "hello"/role:"viewer"/token handshake) - see that
 * file for why that matters (works regardless of the target machine's
 * network/NAT/IP). The difference is what flows over it: instead of JPEG
 * frames + mouse/keyboard, this exchanges terminal bytes.
 *
 * No xterm.js is vendored in this Odoo instance (checked: nothing under
 * odoo/ or addons/ ships it, and this repo's CDN-loading convention - see
 * how Chart.js is pulled in for the dashboards - is for genuinely static
 * libs, not a live third-party terminal engine). Rather than add a new
 * vendored dependency, this implements a small, self-contained ANSI/VT100
 * core: cursor movement, erase line/screen, and basic SGR colors. That
 * covers normal shell use (ls, cd, apt, systemctl, nano, less, top) well.
 * It does not implement the full VT100/xterm spec (e.g. no alternate
 * screen buffer, no mouse reporting) - good enough for admin/IT terminal
 * access, not a drop-in xterm.js replacement.
 */
(function () {
  "use strict";

  var body = document.body;
  var TOKEN = body.dataset.token;
  var RELAY = body.dataset.relay;
  var CUSTOMER = body.dataset.customer || "machine";

  var termEl = document.getElementById("ra-term");
  var overlay = document.getElementById("ra-overlay");
  var overlayText = document.getElementById("ra-overlay-text");
  var statusEl = document.getElementById("ra-status");
  var dotEl = document.getElementById("ra-dot");

  var ws = null;
  var connected = false;

  // ---- terminal core -----------------------------------------------------
  var COLS = 80, ROWS = 24;
  var grid = [];          // grid[row][col] = {ch, cls}
  var cur = { row: 0, col: 0 };
  var curCls = "";        // current SGR state (CSS class), e.g. "fg32 bold"
  var pending = "";       // partial escape sequence carried across chunks

  function blankRow() {
    var row = new Array(COLS);
    for (var i = 0; i < COLS; i++) row[i] = { ch: " ", cls: "" };
    return row;
  }
  function resetGrid() {
    grid = [];
    for (var r = 0; r < ROWS; r++) grid.push(blankRow());
    cur = { row: 0, col: 0 };
  }

  function measureCell() {
    var probe = document.createElement("span");
    probe.style.font = getComputedStyle(termEl).font;
    probe.style.position = "absolute";
    probe.style.visibility = "hidden";
    probe.textContent = "MMMMMMMMMM";
    document.body.appendChild(probe);
    var w = probe.getBoundingClientRect().width / 10;
    document.body.removeChild(probe);
    var lineHeight = parseFloat(getComputedStyle(termEl).lineHeight) || 18;
    return { w: w || 8.4, h: lineHeight };
  }

  function fitToContainer() {
    var cell = measureCell();
    var rect = termEl.getBoundingClientRect();
    var newCols = Math.max(20, Math.floor(rect.width / cell.w) - 1);
    var newRows = Math.max(8, Math.floor(rect.height / cell.h));
    if (newCols === COLS && newRows === ROWS) return false;
    COLS = newCols;
    ROWS = newRows;
    resetGrid();
    return true;
  }

  function scrollUp() {
    grid.shift();
    grid.push(blankRow());
  }

  function writeChar(ch) {
    if (cur.col >= COLS) { cur.col = 0; cur.row++; }
    if (cur.row >= ROWS) { scrollUp(); cur.row = ROWS - 1; }
    grid[cur.row][cur.col] = { ch: ch, cls: curCls };
    cur.col++;
  }

  function eraseInLine(mode) {
    var row = grid[cur.row];
    if (mode === 1) {
      for (var i = 0; i <= cur.col && i < COLS; i++) row[i] = { ch: " ", cls: "" };
    } else if (mode === 2) {
      for (var j = 0; j < COLS; j++) row[j] = { ch: " ", cls: "" };
    } else {
      for (var k = cur.col; k < COLS; k++) row[k] = { ch: " ", cls: "" };
    }
  }

  function eraseInDisplay(mode) {
    if (mode === 2 || mode === 3) {
      resetGrid();
      return;
    }
    if (mode === 1) {
      for (var r = 0; r < cur.row; r++) grid[r] = blankRow();
      eraseInLine(1);
    } else {
      eraseInLine(0);
      for (var r2 = cur.row + 1; r2 < ROWS; r2++) grid[r2] = blankRow();
    }
  }

  var SGR_FG = { 30:1,31:1,32:1,33:1,34:1,35:1,36:1,37:1,90:1,91:1,92:1,93:1,94:1,95:1,96:1,97:1 };

  function applySgr(params) {
    if (!params.length) params = [0];
    var classes = curCls ? curCls.split(" ").filter(Boolean) : [];
    params.forEach(function (p) {
      if (p === 0) { classes = []; }
      else if (p === 1) { if (classes.indexOf("bold") < 0) classes.push("bold"); }
      else if (p === 22) { classes = classes.filter(function (c) { return c !== "bold"; }); }
      else if (p === 7) { if (classes.indexOf("rev") < 0) classes.push("rev"); }
      else if (p === 27) { classes = classes.filter(function (c) { return c !== "rev"; }); }
      else if (p === 39) { classes = classes.filter(function (c) { return c.indexOf("fg") !== 0; }); }
      else if (SGR_FG[p]) {
        classes = classes.filter(function (c) { return c.indexOf("fg") !== 0; });
        classes.push("fg" + p);
      }
    });
    curCls = classes.join(" ");
  }

  // Consumes one escape sequence starting at s[i] === '\x1b'. Returns the
  // index just past it, or -1 if the sequence looks incomplete (caller
  // should stash the remainder in `pending` and wait for more data).
  function consumeEscape(s, i) {
    var n = s.length;
    if (i + 1 >= n) return -1;
    var next = s[i + 1];
    if (next === "]") {
      // OSC ... (BEL or ESC \ terminated) - e.g. window-title sets. Ignored.
      var j = s.indexOf("\x07", i + 2);
      var st = s.indexOf("\x1b\\", i + 2);
      var end = (st !== -1 && (j === -1 || st < j)) ? st + 2 : (j !== -1 ? j + 1 : -1);
      return end === -1 ? -1 : end;
    }
    if (next !== "[") {
      // Unhandled single-char escape (e.g. ESC(B charset select) - skip it.
      return i + 2;
    }
    var k = i + 2;
    while (k < n && !/[A-Za-z@]/.test(s[k])) k++;
    if (k >= n) return -1; // incomplete - wait for more
    var body = s.slice(i + 2, k);
    var final = s[k];
    var params = body.split(";").filter(function (x) { return x !== ""; })
      .map(function (x) { return parseInt(x, 10); });
    switch (final) {
      case "H": case "f":
        cur.row = Math.min(ROWS - 1, Math.max(0, (params[0] || 1) - 1));
        cur.col = Math.min(COLS - 1, Math.max(0, (params[1] || 1) - 1));
        break;
      case "A": cur.row = Math.max(0, cur.row - (params[0] || 1)); break;
      case "B": cur.row = Math.min(ROWS - 1, cur.row + (params[0] || 1)); break;
      case "C": cur.col = Math.min(COLS - 1, cur.col + (params[0] || 1)); break;
      case "D": cur.col = Math.max(0, cur.col - (params[0] || 1)); break;
      case "J": eraseInDisplay(params[0] || 0); break;
      case "K": eraseInLine(params[0] || 0); break;
      case "G": case "`":
        cur.col = Math.min(COLS - 1, Math.max(0, (params[0] || 1) - 1)); break;
      case "d": cur.row = Math.min(ROWS - 1, Math.max(0, (params[0] || 1) - 1)); break;
      case "P": {          // delete characters, shift the rest left
        var np = Math.min(params[0] || 1, COLS - cur.col);
        var rowP = grid[cur.row];
        rowP.splice(cur.col, np);
        for (var q = 0; q < np; q++) rowP.push({ ch: " ", cls: "" });
        break;
      }
      case "@": {          // insert blank characters
        var ni = Math.min(params[0] || 1, COLS - cur.col);
        var rowI = grid[cur.row];
        for (var w = 0; w < ni; w++) rowI.splice(cur.col, 0, { ch: " ", cls: "" });
        rowI.length = COLS;
        break;
      }
      case "X": {          // erase characters (no shift)
        var nx = Math.min(params[0] || 1, COLS - cur.col);
        for (var x = 0; x < nx; x++) grid[cur.row][cur.col + x] = { ch: " ", cls: "" };
        break;
      }
      case "m": applySgr(params); break;
      default: break; // unsupported CSI final byte - consumed, not acted on
    }
    return k + 1;
  }

  function feed(chunk) {
    var s = pending + chunk;
    pending = "";
    var i = 0;
    while (i < s.length) {
      var c = s[i];
      if (c === "\x1b") {
        var next = consumeEscape(s, i);
        if (next === -1) { pending = s.slice(i); break; }
        i = next;
        continue;
      }
      if (c === "\r") { cur.col = 0; i++; continue; }
      if (c === "\n") {
        cur.row++;
        if (cur.row >= ROWS) { scrollUp(); cur.row = ROWS - 1; }
        i++;
        continue;
      }
      if (c === "\b" || c === "\x7f") { cur.col = Math.max(0, cur.col - 1); i++; continue; }
      if (c === "\t") {
        var next8 = Math.min(COLS - 1, (Math.floor(cur.col / 8) + 1) * 8);
        while (cur.col < next8) writeChar(" ");
        i++;
        continue;
      }
      if (c === "\x07") { i++; continue; } // bell - no-op
      if (c.charCodeAt(0) < 0x20) { i++; continue; } // other control chars
      writeChar(c);
      i++;
    }
    render();
  }

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function render() {
    var lines = new Array(ROWS);
    for (var r = 0; r < ROWS; r++) {
      var row = grid[r];
      var out = "";
      var runCls = null, runText = "";
      for (var c = 0; c < COLS; c++) {
        var cell = row[c];
        if (cell.cls !== runCls) {
          if (runCls !== null) {
            out += runCls ? '<span class="' + runCls + '">' + esc(runText) + "</span>" : esc(runText);
          }
          runCls = cell.cls;
          runText = cell.ch;
        } else {
          runText += cell.ch;
        }
      }
      if (runCls !== null) {
        out += runCls ? '<span class="' + runCls + '">' + esc(runText) + "</span>" : esc(runText);
      }
      lines[r] = out;
    }
    termEl.innerHTML = lines.join("\n");
  }

  // ---- connection ----------------------------------------------------
  function setStatus(text, state) {
    statusEl.textContent = text;
    dotEl.className = "ra-dot" + (state ? " ra-" + state : "");
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function sendResize() {
    send({ type: "resize", cols: COLS, rows: ROWS });
  }

  function connect() {
    if (!RELAY) {
      setStatus("no relay configured", "bad");
      overlayText.textContent =
        "Relay URL is not set. Configure asset_remote.relay_url in Odoo.";
      return;
    }
    setStatus("connecting…", "warn");
    try {
      ws = new WebSocket(RELAY);
    } catch (e) {
      setStatus("connection failed", "bad");
      return;
    }
    ws.onopen = function () {
      ws.send(JSON.stringify({ type: "hello", role: "viewer", token: TOKEN }));
    };
    ws.onmessage = function (ev) {
      var obj;
      try { obj = JSON.parse(ev.data); } catch (e) { return; }
      if (obj.type === "welcome") {
        setStatus("waiting for terminal…", "warn");
      } else if (obj.type === "error") {
        setStatus(obj.reason || "rejected", "bad");
        overlayText.textContent = "Relay rejected the session: " + (obj.reason || "unknown");
      } else if (obj.type === "host_info") {
        document.getElementById("ra-title").textContent =
          (obj.os ? obj.os + " • " : "") + CUSTOMER;
      } else if (obj.type === "peer" && obj.event === "leave" && obj.role === "host") {
        setStatus("session ended", "bad");
        overlay.style.display = "grid";
        overlayText.textContent = "The terminal session ended.";
      } else if (obj.type === "output") {
        if (!connected) {
          connected = true;
          overlay.style.display = "none";
          setStatus("connected", "ok");
          focusSink();
        }
        feed(obj.data);
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

  // ---- input -----------------------------------------------------------
  var KEYMAP = {
    Enter: "\r", Backspace: "\x7f", Tab: "\t", Escape: "\x1b",
    ArrowUp: "\x1b[A", ArrowDown: "\x1b[B", ArrowRight: "\x1b[C", ArrowLeft: "\x1b[D",
    Home: "\x1b[H", End: "\x1b[F", Delete: "\x1b[3~",
    PageUp: "\x1b[5~", PageDown: "\x1b[6~",
  };

  // A hidden textarea takes the keyboard focus, so the browser delivers
  // paste (clipboard) events to it - works on plain http too, where
  // navigator.clipboard is not available.
  var sink = document.createElement("textarea");
  sink.setAttribute("aria-label", "terminal input");
  sink.style.cssText = "position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0";
  document.body.appendChild(sink);
  function focusSink() {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed) sink.focus();   // keep a mouse selection for copying
  }
  termEl.setAttribute("tabindex", "0");

  function selectedText() {
    var sel = window.getSelection();
    return sel && !sel.isCollapsed ? sel.toString() : "";
  }
  function copySelection(text) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).catch(function () { document.execCommand("copy"); });
    } else {
      document.execCommand("copy");   // copies the current selection
    }
  }

  document.addEventListener("keydown", function (ev) {
    if (!connected) return;
    var k = ev.key;
    var lower = k.length === 1 ? k.toLowerCase() : k;
    // copy: Ctrl+Shift+C always, Ctrl+C when text is selected
    if (ev.ctrlKey && lower === "c" && (ev.shiftKey || selectedText())) {
      var t = selectedText();
      if (t) copySelection(t);
      ev.preventDefault();
      return;
    }
    // paste: Ctrl+V, Ctrl+Shift+V, Shift+Insert - let the browser paste into the sink
    if ((ev.ctrlKey && lower === "v") || (ev.shiftKey && k === "Insert")) {
      sink.value = "";
      sink.focus();
      return;                 // no preventDefault: the paste event follows
    }
    var data = null;
    if (ev.ctrlKey && !ev.altKey && k.length === 1 && /[a-zA-Z]/.test(k)) {
      data = String.fromCharCode(k.toUpperCase().charCodeAt(0) - 64);   // Ctrl+R, Ctrl+C, Ctrl+D ...
    } else if (ev.ctrlKey && (k === "[" || k === "\\" || k === "]")) {
      data = { "[": "\x1b", "\\": "\x1c", "]": "\x1d" }[k];
    } else if (ev.altKey && !ev.ctrlKey && k.length === 1) {
      data = "\x1b" + k;                                                // Alt+b, Alt+f, Alt+. ...
    } else if (KEYMAP[k] !== undefined) {
      data = KEYMAP[k];
    } else if (k.length === 1 && !ev.metaKey) {
      data = k;
    }
    if (data !== null) {
      ev.preventDefault();    // keeps Ctrl+R / Ctrl+W / Ctrl+L away from the browser where it can
      send({ type: "input", data: data });
      if (document.activeElement !== sink) focusSink();
    }
  }, true);

  sink.addEventListener("paste", function (ev) {
    var text = (ev.clipboardData || window.clipboardData).getData("text");
    ev.preventDefault();
    sink.value = "";
    if (text && connected) send({ type: "input", data: text.replace(/\r?\n/g, "\r") });
  });
  termEl.addEventListener("paste", function (ev) {
    var text = (ev.clipboardData || window.clipboardData).getData("text");
    ev.preventDefault();
    if (text && connected) send({ type: "input", data: text.replace(/\r?\n/g, "\r") });
  });
  termEl.addEventListener("mouseup", function () { setTimeout(focusSink, 0); });
  // right-click pastes too, like most terminals
  termEl.addEventListener("contextmenu", function (ev) {
    if (selectedText()) return;   // leave the menu for copying a selection
    if (navigator.clipboard && window.isSecureContext && navigator.clipboard.readText) {
      ev.preventDefault();
      navigator.clipboard.readText().then(function (t) {
        if (t && connected) send({ type: "input", data: t.replace(/\r?\n/g, "\r") });
      });
    }
  });

  window.addEventListener("resize", function () {
    if (fitToContainer()) { render(); sendResize(); }
  });

  document.getElementById("ra-end").addEventListener("click", function () {
    send({ type: "end_session" });
    if (ws) ws.close();
    setStatus("session ended", "bad");
    overlay.style.display = "grid";
    overlayText.textContent = "You ended the session.";
  });

  // keepalive
  setInterval(function () { send({ type: "ping" }); }, 20000);

  fitToContainer();
  render();
  connect();
})();
