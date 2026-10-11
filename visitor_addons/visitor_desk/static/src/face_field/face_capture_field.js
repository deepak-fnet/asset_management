import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { imageUrl } from "@web/core/utils/urls";
import { isBinarySize } from "@web/core/utils/binary";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";
import { CameraDialog } from "../camera/camera_dialog";

/** Image field with a big round preview and Capture / Retake / Remove buttons (webcam). */
export class FaceCaptureField extends Component {
    static template = "visitor_desk.FaceCaptureField";
    static props = { ...standardFieldProps };

    setup() {
        this.dialog = useService("dialog");
    }

    get value() {
        return this.props.record.data[this.props.name];
    }

    get src() {
        if (!this.value) {
            return false;
        }
        if (isBinarySize(this.value)) {
            return imageUrl(this.props.record.resModel, this.props.record.resId, this.props.name, {
                unique: this.props.record.data.write_date,
            });
        }
        return `data:image/jpeg;base64,${this.value}`;
    }

    capture() {
        this.dialog.add(CameraDialog, {
            title: _t("Capture Visitor Face"),
            onCapture: (b64) => this.props.record.update({ [this.props.name]: b64 }),
        });
    }

    remove() {
        this.props.record.update({ [this.props.name]: false });
    }
}

registry.category("fields").add("face_capture", {
    component: FaceCaptureField,
    displayName: _t("Face Capture"),
    supportedTypes: ["binary"],
    fieldDependencies: [{ name: "write_date", type: "datetime" }],
    isEmpty: () => false,
});
