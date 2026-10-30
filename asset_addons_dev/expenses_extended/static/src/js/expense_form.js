/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { onMounted, onPatched } from "@odoo/owl";

// Patch the FormController to add custom behavior for hr.expense forms
patch(FormController.prototype, {
    setup() {
        super.setup();

        // Apply color when form is mounted
        onMounted(() => {
            if (this.props.resModel === "hr.expense") {
                this.applyExpenseFormColor();
            }
        });

        // Reapply color when form is updated
        onPatched(() => {
            if (this.props.resModel === "hr.expense") {
                this.applyExpenseFormColor();
            }
        });
    },

    /**
     * Apply color to expense form based on line_color field
     */
    applyExpenseFormColor() {
        try {
            const record = this.model?.root;

            if (!record || !record.data) {
                return;
            }

            const lineColor = record.data.line_color;
            const formSheet = document.querySelector('.o_form_sheet');

            if (!formSheet) {
                return;
            }

            // Remove all color classes first
            formSheet.classList.remove('expense_color_green', 'expense_color_yellow', 'expense_color_red');

            // Apply the appropriate color class based on line_color
            if (lineColor === 'green') {
                formSheet.classList.add('expense_color_green');
                this.addStatusBadge('Fully Paid', 'paid');
            } else if (lineColor === 'yellow') {
                formSheet.classList.add('expense_color_yellow');
                this.addStatusBadge('Partially Paid', 'partial');
            } else if (lineColor === 'red') {
                formSheet.classList.add('expense_color_red');
                this.addStatusBadge('Balance Due', 'pending');
            }

            // Highlight the balance amount field
            this.highlightBalanceField(record.data);

        } catch (error) {
            console.error('Error applying expense form color:', error);
        }
    },

    /**
     * Add a status badge to indicate payment status
     */
    addStatusBadge(text, statusClass) {
        // Remove existing badge if any
        const existingBadge = document.querySelector('.expense_status_indicator');
        if (existingBadge) {
            existingBadge.remove();
        }

        // Create new badge
        const badge = document.createElement('div');
        badge.className = `expense_status_indicator ${statusClass}`;
        badge.textContent = text;

        // Add to form
        const formSheet = document.querySelector('.o_form_sheet');
        if (formSheet) {
            formSheet.style.position = 'relative';
            formSheet.appendChild(badge);
        }
    },

    /**
     * Highlight the balance amount field
     */
    highlightBalanceField(data) {
        const balanceField = document.querySelector('div[name="balance_amount"]');

        if (balanceField) {
            const balanceAmount = data.balance_amount || 0;

            // Add highlight class
            balanceField.classList.add('balance_amount_highlight');

            // Remove all status classes first
            balanceField.classList.remove('positive', 'negative', 'partial');

            // Add appropriate class based on balance
            if (balanceAmount <= 0) {
                balanceField.classList.add('positive');
            } else if (data.advance_pay && data.advance_pay > 0) {
                balanceField.classList.add('partial');
            } else {
                balanceField.classList.add('negative');
            }
        }
    },

    /**
     * Override onRecordSaved to reapply colors after save
     */
    async onRecordSaved(record) {
        await super.onRecordSaved(record);

        if (this.props.resModel === "hr.expense") {
            // Small delay to ensure the record is fully updated
            setTimeout(() => {
                this.applyExpenseFormColor();
            }, 100);
        }
    }
});

console.log('HR Expense Custom Form Color module loaded');