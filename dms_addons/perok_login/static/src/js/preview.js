/* Live preview (Settings > Login Page > Animations): this login page runs in an
   iframe of the animation form. The form posts the page CSS, font and texts of
   its unsaved values; nothing here logs in. */
(function () {
    "use strict";
    // never post the form from the preview
    window.addEventListener("submit", function (e) {
        e.preventDefault();
        e.stopImmediatePropagation();
    }, true);
    window.addEventListener("click", function (e) {
        var a = e.target.closest && e.target.closest("a[href]");
        if (a) {
            e.preventDefault();
        }
    }, true);

    window.addEventListener("message", function (e) {
        if (e.origin !== location.origin || !e.data || e.data.type !== "perok-preview") {
            return;
        }
        var data = e.data;
        var style = document.getElementById("pk_theme");
        if (!style) {
            style = document.createElement("style");
            style.id = "pk_theme";
            document.head.appendChild(style);
        }
        style.textContent = data.css || "";
        var font = document.getElementById("pk_font");
        if (data.font) {
            if (!font) {
                font = document.createElement("link");
                font.id = "pk_font";
                font.rel = "stylesheet";
                document.head.appendChild(font);
            }
            if (font.getAttribute("href") !== data.font) {
                font.setAttribute("href", data.font);
            }
        }
        var texts = data.texts || {};
        document.querySelectorAll("[data-pk-text]").forEach(function (el) {
            var key = el.getAttribute("data-pk-text");
            if (key in texts) {
                el.textContent = texts[key];
            }
        });
        document.querySelectorAll("[data-pk-ph]").forEach(function (el) {
            var key = el.getAttribute("data-pk-ph");
            if (key in texts) {
                el.setAttribute("placeholder", texts[key]);
            }
        });
    });
    window.parent.postMessage({ type: "perok-preview-ready" }, location.origin);
})();
