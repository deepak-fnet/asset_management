/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * Vendor Comparison grid: products as rows, invited vendors as columns,
 * each cell a checkbox (award this vendor this product) next to that
 * vendor's quoted price (read-only - prices come from the vendors' own
 * quotations, this screen only decides who wins). No standard Odoo view
 * renders a product x vendor matrix, so this is a plain Owl client action
 * driven by one RPC (purchase.order.get_comparison_grid_data).
 *
 * The top bar just lists the vendors who bid. Awarding happens two ways:
 *   - a cell checkbox: award / withdraw that one product for that vendor
 *   - a column header checkbox: award that vendor every product they
 *     quoted ("one vendor for all"), or withdraw all of theirs when
 *     unticked. It shows ticked only while the vendor holds every product
 *     it quoted.
 */
export class VendorComparisonGrid extends Component {
    static template = "vendor_management.VendorComparisonGrid";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.poId = this.props.action.context.active_id;
        this.state = useState({
            loading: true,
            poName: "",
            poState: "",
            locked: false,
            lockedReason: "",
            confirmed: false,
            canConfirm: false,
            currencySymbol: "",
            currencyPosition: "before",
            vendors: [],          // [{id, name, subtitle, quote_id, payment_term, delivery_date, note,
                                  //   attachment_count, quoted_count, on_time_pct, rating, receipt_count}]
            busy: false,
            products: [],         // [{line_id, product_id, product_name, qty, uom, cellsByVendor}]
        });
        onWillStart(() => this.load());
    }

    async load() {
        const data = await this.orm.call(
            "purchase.order", "get_comparison_grid_data", [[this.poId]]
        );
        this.state.poName = data.po_name;
        this.state.poState = data.po_state;
        this.state.locked = data.locked;
        this.state.lockedReason = data.locked_reason;
        this.state.confirmed = data.confirmed;
        this.state.canConfirm = data.can_confirm;
        this.state.currencySymbol = data.currency_symbol;
        this.state.currencyPosition = data.currency_position;
        this.state.vendors = data.vendors;
        this.state.products = data.products.map((p) => {
            const cellsByVendor = {};
            for (const cell of p.cells) {
                cellsByVendor[cell.vendor_id] = cell;
            }
            return { ...p, cellsByVendor };
        });
        this.state.loading = false;
    }

    productImageUrl(product) {
        return `/web/image/product.product/${product.product_id}/image_128`;
    }

    formatAmount(amount) {
        const number = (amount || 0).toLocaleString(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
        });
        const symbol = this.state.currencySymbol;
        return this.state.currencyPosition === "after" ? `${number} ${symbol}` : `${symbol} ${number}`;
    }

    /** True for the cheapest quoted unit price on this product (ties included). */
    isLowestPrice(product, cell) {
        const prices = Object.values(product.cellsByVendor)
            .map((c) => c.price)
            .filter((p) => p > 0);
        return prices.length > 1 && cell.price > 0 && cell.price === Math.min(...prices);
    }

    // ---- per product -------------------------------------------------

    _quotedCells(product) {
        return Object.values(product.cellsByVendor).filter((c) => c.price > 0);
    }

    lowestPriceOf(product) {
        const cells = this._quotedCells(product);
        return cells.length ? Math.min(...cells.map((c) => c.price)) : 0;
    }

    /** "+12% (+₹6,000)" above the cheapest quote for this product, or "". */
    priceDiffLabel(product, cell) {
        const low = this.lowestPriceOf(product);
        if (!cell || !low || cell.price <= low || this._quotedCells(product).length < 2) {
            return "";
        }
        const pct = Math.round(((cell.price - low) / low) * 100);
        return `+${pct}% (+${this.formatAmount((cell.price - low) * product.qty)})`;
    }

    /** Earliest delivery date quoted for this product (ties included). */
    isFastest(product, cell) {
        const dates = this._quotedCells(product).map((c) => c.delivery_date).filter(Boolean).sort();
        return dates.length > 1 && cell.delivery_date && cell.delivery_date === dates[0]
            && dates[0] !== dates[dates.length - 1];
    }

    // ---- selection summary ------------------------------------------------

    /** The order if every product went to its cheapest quote. */
    get lowestPossibleTotal() {
        return this.state.products.reduce((sum, p) => sum + this.lowestPriceOf(p) * p.qty, 0);
    }

    get highestPossibleTotal() {
        return this.state.products.reduce((sum, p) => {
            const cells = this._quotedCells(p);
            return cells.length ? sum + Math.max(...cells.map((c) => c.price)) * p.qty : sum;
        }, 0);
    }

    get allSelected() {
        return this.state.products.length > 0 && this.selectedCount === this.state.products.length;
    }

    get selectedVendorCount() {
        const ids = new Set();
        for (const p of this.state.products) {
            const winner = Object.values(p.cellsByVendor).find((c) => c.is_winning);
            if (winner) {
                ids.add(winner.vendor_id);
            }
        }
        return ids.size;
    }

    get selectedPct() {
        return this.state.products.length ? Math.round((this.selectedCount / this.state.products.length) * 100) : 0;
    }

    // ---- smart picks --------------------------------------------------------

    async _award(cells) {
        const ids = cells.filter(Boolean).map((c) => c.quote_line_id);
        if (!ids.length) {
            return;
        }
        this.state.busy = true;
        await this._call("purchase.order", "action_award_quote_lines", [[this.poId], ids]);
        this.state.busy = false;
    }

    async pickLowest() {
        await this._award(this.state.products.map((p) => {
            const cells = this._quotedCells(p);
            // cheapest; earliest delivery breaks a tie
            return cells.sort((a, b) => a.price - b.price
                || (a.delivery_date || "9999").localeCompare(b.delivery_date || "9999"))[0];
        }));
    }

    async pickFastest() {
        await this._award(this.state.products.map((p) => {
            const cells = this._quotedCells(p);
            return cells.sort((a, b) => (a.delivery_date || "9999").localeCompare(b.delivery_date || "9999")
                || a.price - b.price)[0];
        }));
    }

    async clearAll() {
        this.state.busy = true;
        await this._call("purchase.order", "action_clear_awards", [[this.poId]]);
        this.state.busy = false;
    }

    /** What this vendor's bid would cost for everything they quoted. */
    vendorTotal(vendorId) {
        return this.state.products.reduce((sum, product) => {
            const cell = product.cellsByVendor[vendorId];
            return cell ? sum + cell.price * product.qty : sum;
        }, 0);
    }

    isLowestTotal(vendorId) {
        if (this.state.vendors.length < 2) {
            return false;
        }
        const totals = this.state.vendors.map((v) => this.vendorTotal(v.id)).filter((t) => t > 0);
        const mine = this.vendorTotal(vendorId);
        return mine > 0 && mine === Math.min(...totals);
    }

    /** What the order costs with the vendors currently ticked. */
    get selectedTotal() {
        return this.state.products.reduce((sum, product) => {
            const winner = Object.values(product.cellsByVendor).find((c) => c.is_winning);
            return winner ? sum + winner.price * product.qty : sum;
        }, 0);
    }

    get selectedCount() {
        return this.state.products.filter((p) =>
            Object.values(p.cellsByVendor).some((c) => c.is_winning)
        ).length;
    }

    /** Ticked only while this vendor holds every product it quoted. */
    isVendorAwardedAll(vendorId) {
        const cells = this.state.products
            .map((p) => p.cellsByVendor[vendorId])
            .filter(Boolean);
        return cells.length > 0 && cells.every((c) => c.is_winning);
    }

    async _call(model, method, args) {
        try {
            await this.orm.call(model, method, args);
        } catch (error) {
            this.notification.add(error.data?.message || error.message, { type: "danger" });
        }
        await this.load();
    }

    async toggleCell(cell) {
        if (!cell) {
            return;
        }
        const method = cell.is_winning ? "action_unselect_line" : "action_select_line";
        await this._call("vendor.quote.line", method, [[cell.quote_line_id]]);
    }

    async toggleVendorAll(vendorId) {
        const method = this.isVendorAwardedAll(vendorId)
            ? "action_unaward_all_from_vendor"
            : "action_award_all_to_vendor";
        await this._call("purchase.order", method, [[this.poId], vendorId]);
    }

    /** Confirm the selection (server checks every product has a vendor), then
     * go back to the RFQ form, where Submit for Approval is now available. */
    async confirmSelection() {
        try {
            await this.orm.call("purchase.order", "action_confirm_comparison", [[this.poId]]);
        } catch (error) {
            this.notification.add(error.data?.message || error.message, { type: "danger" });
            return;
        }
        this.notification.add("Vendor selection confirmed. You can now submit it for approval.", {
            type: "success",
        });
        await this.goBack();
    }

    async goBack() {
        try {
            await this.actionService.restore();
        } catch {
            // No previous screen in the breadcrumb (e.g. opened after a
            // page refresh) - go to the RFQ itself instead.
            await this.actionService.doAction({
                type: "ir.actions.act_window",
                res_model: "purchase.order",
                res_id: this.poId,
                views: [[false, "form"]],
            });
        }
    }
}

registry.category("actions").add("vendor_management.vendor_comparison_grid", VendorComparisonGrid);
