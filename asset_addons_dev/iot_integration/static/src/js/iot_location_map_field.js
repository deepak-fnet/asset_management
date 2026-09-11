/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Shows the reading's latitude/longitude as an embedded Google Map pin.
 * Attached to the "latitude" field but reads both latitude and longitude
 * off the record, since a single record can only carry one widget field.
 */
class IotLocationMapField extends Component {
    static template = "iot_integration.LocationMapField";
    static props = { ...standardFieldProps };

    get latitude() {
        return this.props.record.data.latitude;
    }

    get longitude() {
        return this.props.record.data.longitude;
    }

    get hasLocation() {
        return !!(this.latitude || this.longitude);
    }

    get embedUrl() {
        return `https://maps.google.com/maps?q=${this.latitude},${this.longitude}&z=15&output=embed`;
    }

    get mapsLink() {
        return `https://www.google.com/maps?q=${this.latitude},${this.longitude}`;
    }
}

registry.category("fields").add("iot_location_map", { component: IotLocationMapField });

export default IotLocationMapField;
