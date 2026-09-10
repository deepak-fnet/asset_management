// Plain DOM only, deliberately - this runs on a public website page, not in
// the backend, so it doesn't pull in the OWL/backend asset bundle. No
// imports/exports here, so this is a classic script, not an @odoo-module.

document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('.o_ticket_form');
    if (!form) {
        return;
    }

    // Live character counter for the issue description.
    const notes = form.querySelector('#notes');
    const counter = document.getElementById('o_ticket_notes_count');
    if (notes && counter) {
        const updateCount = () => {
            counter.textContent = notes.value.length;
        };
        notes.addEventListener('input', updateCount);
        updateCount();
    }

    // Disable the submit button and show a spinner on submit, so a slow
    // connection doesn't invite a double-click double-submission.
    form.addEventListener('submit', () => {
        const button = form.querySelector('.o_ticket_submit_btn');
        if (!button) {
            return;
        }
        button.disabled = true;
        const spinner = button.querySelector('.o_btn_spinner');
        if (spinner) {
            spinner.style.display = 'inline-block';
        }
    });
});
