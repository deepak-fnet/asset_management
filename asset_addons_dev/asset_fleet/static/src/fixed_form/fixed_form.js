/** Fixed asset form: clicking a pulse item opens its tab. */
document.addEventListener("click", (ev) => {
    const item = ev.target.closest && ev.target.closest(".ga-pulse [data-ga-tab]");
    if (!item) {
        return;
    }
    const form = item.closest(".o_form_view");
    const tab = form && form.querySelector(`.o_notebook .nav-link[name="${item.dataset.gaTab}"]`);
    if (tab) {
        tab.click();
        tab.scrollIntoView({ behavior: "smooth", block: "center" });
    }
});
