/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const SPARK_W = 240;
const SPARK_H = 64;
const SPARK_PAD = 8;

/**
 * Price Comparison dashboard (the RFQ/PO "Price Comparison" button): per
 * product, an overview (lowest / average price, quantity bought), one card
 * per vendor with its price range and trend, on-time delivery and rating
 * charts, and the full price history of whichever vendor card is clicked.
 * One RPC: purchase.order.get_price_comparison_data.
 */
export class PriceComparisonDashboard extends Component {
    static template = "vendor_management.PriceComparisonDashboard";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.poId = this.props.action.context.active_id;
        this.state = useState({
            loading: true,
            poName: "",
            currencySymbol: "",
            currencyPosition: "before",
            products: [],
            productIndex: 0,
            selectedVendorId: null,
        });
        onWillStart(() => this.load());
    }

    async load() {
        const data = await this.orm.call("purchase.order", "get_price_comparison_data", [[this.poId]]);
        this.state.poName = data.po_name;
        this.state.currencySymbol = data.currency_symbol;
        this.state.currencyPosition = data.currency_position;
        this.state.products = data.products;
        this.state.loading = false;
        this._selectDefaultVendor();
    }

    get product() {
        return this.state.products[this.state.productIndex];
    }

    get selectedVendor() {
        return this.product?.vendors.find((v) => v.id === this.state.selectedVendorId);
    }

    _selectDefaultVendor() {
        const vendors = this.product?.vendors || [];
        this.state.selectedVendorId = vendors.length ? vendors[0].id : null;
    }

    selectProduct(index) {
        this.state.productIndex = index;
        this._selectDefaultVendor();
    }

    selectVendor(vendorId) {
        this.state.selectedVendorId = vendorId;
    }

    onVendorKeydown(ev, vendorId) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.selectVendor(vendorId);
        }
    }

    // ---------------------------------------------------------------- format
    formatAmount(amount) {
        const number = (amount || 0).toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
        const symbol = this.state.currencySymbol;
        return this.state.currencyPosition === "after" ? `${number} ${symbol}` : `${symbol} ${number}`;
    }

    formatQty(qty) {
        return (qty || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
    }

    colorClass(vendor) {
        return vendor.color_slot >= 0 ? `o_pcd_c${vendor.color_slot}` : "o_pcd_cneutral";
    }

    productImageUrl(product) {
        return `/web/image/product.product/${product.product_id}/image_256`;
    }

    // ------------------------------------------------------------ sparkline
    /** Shared y-scale across every vendor of this product, so the cards'
     * trend lines are comparable with each other, not each self-scaled. */
    get priceRange() {
        const prices = (this.product?.vendors || []).flatMap((v) => v.history.map((h) => h.price));
        if (!prices.length) {
            return { lo: 0, hi: 1 };
        }
        let lo = Math.min(...prices);
        let hi = Math.max(...prices);
        if (lo === hi) {
            lo = lo * 0.9;
            hi = hi * 1.1 || 1;
        }
        return { lo, hi };
    }

    sparkPoints(vendor) {
        const points = [...vendor.history].reverse(); // chronological
        const { lo, hi } = this.priceRange;
        const innerW = SPARK_W - SPARK_PAD * 2;
        const innerH = SPARK_H - SPARK_PAD * 2;
        return points.map((p, i) => {
            const x = points.length === 1 ? SPARK_W / 2 : SPARK_PAD + (innerW * i) / (points.length - 1);
            const y = SPARK_PAD + innerH - (innerH * (p.price - lo)) / (hi - lo);
            return {
            x,
            y,
            style: `left: ${((x / SPARK_W) * 100).toFixed(2)}%; top: ${((y / SPARK_H) * 100).toFixed(2)}%;`,
            tip: `${p.date || ""} · ${p.kind === "bid" ? "Bid" : "Order"} ${p.ref} · ${this.formatAmount(p.price)}`,
            key: `${p.ref}_${i}`,
            };
        });
    }

    sparkPath(vendor) {
        return this.sparkPoints(vendor).map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    }

    get sparkViewBox() {
        return `0 0 ${SPARK_W} ${SPARK_H}`;
    }

    // ---------------------------------------------------------- performance
    /** Bar height as a % of a 0-100 scale; a 2% floor keeps a real 0%
     * bar visible as a sliver rather than vanishing. */
    barStyle(vendor, key) {
        return `height: ${Math.max(Number(vendor[key]) || 0, 2)}%`;
    }

    metricVendors(key) {
        return (this.product?.vendors || []).filter((v) => v[key] !== null && v[key] !== undefined);
    }

    async goBack() {
        try {
            await this.actionService.restore();
        } catch {
            await this.actionService.doAction({
                type: "ir.actions.act_window",
                res_model: "purchase.order",
                res_id: this.poId,
                views: [[false, "form"]],
            });
        }
    }
}

registry.category("actions").add("vendor_management.price_comparison_dashboard", PriceComparisonDashboard);
