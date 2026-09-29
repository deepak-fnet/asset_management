/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { _t } from "@web/core/l10n/translation";
import * as BarcodeScanner from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";

/**
 * "Scan" button for the Physical Verification form. Opens the device
 * camera (core's own BarcodeDialog/ZXing detector - the same mechanism
 * many2one fields use for their inline barcode icon), reads the printed
 * QR label (see asset.asset._compute_qr_code: "Asset: <code>\nSerial:
 * <serial>"), and asks the server to mark the matching line Found.
 *
 * Gated on camera support only (isBarcodeScannerSupported), not
 * isMobileOS() - the primary use case is a phone walking the floor, but
 * there is no reason to also block a desktop with a webcam.
 */
class PhysicalVerificationScanner extends Component {
    static template = "general_asset.PhysicalVerificationScanner";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
    }

    get isSupported() {
        return isBarcodeScannerSupported();
    }

    async onScanClick() {
        let code;
        try {
            code = await BarcodeScanner.scanBarcode(this.env);
        } catch (error) {
            this.notification.add(error.message || _t("Could not access the camera."), {
                type: "danger",
            });
            return;
        }
        if (!code) {
            this.notification.add(_t("Please scan again."), { type: "warning" });
            return;
        }
        if ("vibrate" in navigator) {
            navigator.vibrate(100);
        }
        const result = await this.orm.call(
            "physical.verification", "action_scan_found",
            [[this.props.record.resId], code]
        );
        this.notification.add(result.message, {
            type: result.success ? (result.already ? "warning" : "success") : "danger",
        });
        if (result.success) {
            await this.props.record.load();
        }
    }
}

registry.category("view_widgets").add(
    "physical_verification_scanner", { component: PhysicalVerificationScanner }
);
