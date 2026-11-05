/** Fixed asset form - small interactions on the computed visuals:
 *  pulse item -> its tab, action chips -> the real form buttons,
 *  count-up numbers, and a hover crosshair on the depreciation chart. */

function openTab(form, name) {
    const tab = name && form.querySelector(`.o_notebook .nav-link[name="${name}"]`);
    if (tab) {
        tab.click();
    }
    return tab;
}

function realButton(form, name) {
    return [...form.querySelectorAll(`button[name="${name}"]`)].find(
        (b) => !b.closest(".ga-acts") && b.offsetParent !== null
    );
}

document.addEventListener("click", (ev) => {
    if (!ev.target.closest) {
        return;
    }
    const chip = ev.target.closest(".ga-acts [data-ga-btn]");
    const form = ev.target.closest(".o_form_view");
    if (chip && form) {
        ev.preventDefault();
        let btn = realButton(form, chip.dataset.gaBtn);
        if (!btn && chip.dataset.gaTab) {
            openTab(form, chip.dataset.gaTab);
            setTimeout(() => {
                const b = realButton(form, chip.dataset.gaBtn);
                b ? b.click() : chip.classList.add("ga-nope");
            }, 250);
            return;
        }
        btn ? btn.click() : chip.classList.add("ga-nope");
        return;
    }
    const item = ev.target.closest(".ga-pulse [data-ga-tab]");
    if (item && form) {
        const tab = openTab(form, item.dataset.gaTab);
        if (tab) {
            tab.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }
});

// chips for buttons this record does not offer are hidden; numbers count up
function decorate(root) {
    root.querySelectorAll(".ga-acts:not(.ga-done)").forEach((bar) => {
        bar.classList.add("ga-done");
        const form = bar.closest(".o_form_view");
        if (!form) {
            return;
        }
        bar.querySelectorAll("[data-ga-btn]").forEach((chip) => {
            const has =
                realButton(form, chip.dataset.gaBtn) ||
                (chip.dataset.gaTab && form.querySelector(`.o_notebook .nav-link[name="${chip.dataset.gaTab}"]`));
            chip.hidden = !has;
        });
    });
    root.querySelectorAll("[data-count]:not(.ga-counted)").forEach((el) => {
        el.classList.add("ga-counted");
        const target = parseFloat(el.dataset.count);
        const final = el.textContent;
        const m = final.match(/^([^\d-]*)/);
        const prefix = m ? m[1] : "";
        if (!isFinite(target) || target <= 0) {
            return;
        }
        const t0 = performance.now();
        const dur = 700;
        const step = (t) => {
            const k = Math.min((t - t0) / dur, 1);
            const v = target * (1 - Math.pow(1 - k, 3));
            el.textContent = k < 1 ? prefix + Math.round(v).toLocaleString("en-IN") : final;
            if (k < 1) {
                requestAnimationFrame(step);
            }
        };
        requestAnimationFrame(step);
    });
}

new MutationObserver(() => decorate(document)).observe(document.documentElement, {
    childList: true,
    subtree: true,
});

// depreciation chart: crosshair + tooltip
document.addEventListener("mousemove", (ev) => {
    const chart = ev.target.closest && ev.target.closest(".ga-chart");
    document.querySelectorAll(".ga-chart.ga-hover").forEach((c) => c !== chart && c.classList.remove("ga-hover"));
    if (!chart) {
        return;
    }
    let pts = chart._pts;
    if (!pts) {
        try {
            pts = chart._pts = JSON.parse(chart.dataset.pts || "[]");
        } catch {
            return;
        }
    }
    if (!pts.length) {
        return;
    }
    const r = chart.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * 100;
    let best = pts[0];
    for (const p of pts) {
        if (Math.abs(p.x - px) < Math.abs(best.x - px)) {
            best = p;
        }
    }
    chart.classList.add("ga-hover");
    const cross = chart.querySelector(".ga-cross");
    const tip = chart.querySelector(".ga-tip");
    cross.style.left = best.x + "%";
    tip.style.left = Math.min(Math.max(best.x, 12), 88) + "%";
    tip.innerHTML = "";
    const h = document.createElement("b");
    h.textContent = best.l;
    const v = document.createElement("span");
    v.textContent = "Book value " + best.v;
    tip.append(h, v);
    if (best.d) {
        const d = document.createElement("span");
        d.className = "dep";
        d.textContent = "− " + best.d + " this year";
        tip.append(d);
    }
    chart.querySelectorAll(".ga-dot").forEach((dot) => {
        dot.classList.toggle("hot", dot.style.left === best.x.toFixed(2) + "%" || parseFloat(dot.style.left) === best.x);
    });
});
