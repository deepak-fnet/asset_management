/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUpdateProps, useRef } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

console.log("🗺️ [Asset Management] asset_map_widget.js: Module loaded");

// CDN URLs for Leaflet
const LEAFLET_CSS_CDN = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const LEAFLET_JS_CDN = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";

/**
 * Dynamically load a script from URL
 */
function loadScript(src) {
    return new Promise((resolve, reject) => {
        if (document.querySelector(`script[src="${src}"]`)) {
            resolve();
            return;
        }
        const script = document.createElement("script");
        script.src = src;
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

/**
 * Dynamically load a CSS file from URL
 */
function loadCSS(href) {
    return new Promise((resolve) => {
        if (document.querySelector(`link[href="${href}"]`)) {
            resolve();
            return;
        }
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = href;
        link.onload = resolve;
        document.head.appendChild(link);
    });
}

/**
 * OpenStreetMap Widget for Odoo 18
 *
 * Displays an embedded OpenStreetMap showing the asset's location.
 * Uses Leaflet.js for interactive map rendering.
 *
 * Field names in database: latitude, longitude (NOT location_latitude)
 */
class AssetMapWidget extends Component {
    static template = "asset_management.AssetMapWidget";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        console.log("🗺️ [Asset Management] AssetMapWidget: setup() called");
        this.mapContainer = useRef("mapContainer");
        this.map = null;
        this.marker = null;

        onMounted(async () => {
            console.log("🗺️ [Asset Management] AssetMapWidget: onMounted, coordinates:", this.latitude, this.longitude);
            await this.ensureLeafletLoaded();
            this.initMap();
        });

        onWillUpdateProps((nextProps) => {
            // Update map when coordinates change
            // Support both field naming conventions
            const newLat = nextProps.record.data.latitude || nextProps.record.data.gps_latitude || nextProps.record.data.location_latitude;
            const newLon = nextProps.record.data.longitude || nextProps.record.data.gps_longitude || nextProps.record.data.location_longitude;
            if (this.map && newLat && newLon) {
                this.updateMapPosition(newLat, newLon);
            }
        });
    }

    async ensureLeafletLoaded() {
        if (typeof L !== "undefined") {
            console.log("🗺️ [Asset Management] Leaflet already loaded");
            return;
        }

        console.log("🗺️ [Asset Management] Loading Leaflet from CDN...");
        try {
            await loadCSS(LEAFLET_CSS_CDN);
            await loadScript(LEAFLET_JS_CDN);
            console.log("🗺️ [Asset Management] Leaflet loaded successfully");
        } catch (error) {
            console.error("🗺️ [Asset Management] Failed to load Leaflet:", error);
        }
    }

    /**
     * Get latitude from record data - supports multiple field naming conventions
     */
    get latitude() {
        const data = this.props.record.data;
        // Try different field names (database uses 'latitude')
        const lat = data.latitude || data.gps_latitude || data.location_latitude || 0;
        console.log("🗺️ [Asset Management] Getting latitude:", lat, "from data:", {
            latitude: data.latitude,
            gps_latitude: data.gps_latitude,
            location_latitude: data.location_latitude
        });
        return lat;
    }

    /**
     * Get longitude from record data - supports multiple field naming conventions
     */
    get longitude() {
        const data = this.props.record.data;
        // Try different field names (database uses 'longitude')
        const lon = data.longitude || data.gps_longitude || data.location_longitude || 0;
        console.log("🗺️ [Asset Management] Getting longitude:", lon, "from data:", {
            longitude: data.longitude,
            gps_longitude: data.gps_longitude,
            location_longitude: data.location_longitude
        });
        return lon;
    }

    /**
     * Check if we have valid coordinates (not 0,0)
     */
    get hasValidCoordinates() {
        const lat = this.latitude;
        const lon = this.longitude;
        // Consider valid if either lat or lon is non-zero
        const valid = (lat !== 0 || lon !== 0) && lat !== null && lon !== null && lat !== undefined && lon !== undefined;
        console.log("🗺️ [Asset Management] hasValidCoordinates:", valid, "lat:", lat, "lon:", lon);
        return valid;
    }

    get assetName() {
        const data = this.props.record.data;
        return data.device_name || data.asset_name || data.name || "Asset";
    }

    get locationSource() {
        return this.props.record.data.location_source || "unknown";
    }

    get locationInfo() {
        const data = this.props.record.data;
        const city = data.city || "";
        const region = data.region || "";
        const country = data.country || "";
        return [city, region, country].filter(Boolean).join(", ");
    }

    initMap() {
        if (!this.mapContainer.el) {
            console.warn("🗺️ [Asset Management] Map container not found");
            return;
        }

        if (!this.hasValidCoordinates) {
            console.log("🗺️ [Asset Management] No valid coordinates, skipping map init");
            return;
        }

        // Check if Leaflet is loaded
        if (typeof L === "undefined") {
            console.error("🗺️ [Asset Management] Leaflet library not loaded");
            return;
        }

        const lat = this.latitude;
        const lon = this.longitude;
        console.log(`🗺️ [Asset Management] Initializing map at ${lat}, ${lon}`);

        // Fix Leaflet's default icon path issue when loaded from CDN
        delete L.Icon.Default.prototype._getIconUrl;
        L.Icon.Default.mergeOptions({
            iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
            iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
            shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
        });

        // Initialize map
        this.map = L.map(this.mapContainer.el, {
            scrollWheelZoom: false,
            dragging: true,
            zoomControl: true,
        }).setView([lat, lon], 15);

        // Add OpenStreetMap tile layer
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            maxZoom: 19,
        }).addTo(this.map);

        // Add marker
        this.marker = L.marker([lat, lon]).addTo(this.map);

        // Add popup with asset info
        const popupContent = this.getPopupContent(lat, lon);
        this.marker.bindPopup(popupContent).openPopup();

        // Fix map display issues after container is visible
        setTimeout(() => {
            if (this.map) {
                this.map.invalidateSize();
            }
        }, 100);
    }

    getPopupContent(lat, lon) {
        const assetName = this.assetName;
        const locationInfo = this.locationInfo;
        const source = this.locationSource;

        let sourceLabel = "";
        let sourceColor = "#64748b";
        if (source === "gps" || source === "windows" || source === "device") {
            sourceLabel = "📍 GPS/Device Location";
            sourceColor = "#16a34a";
        } else if (source === "ip") {
            sourceLabel = "🌐 Network Location (Approximate)";
            sourceColor = "#d97706";
        }

        return `
            <div style="min-width: 180px;">
                <strong style="display: block; margin-bottom: 4px; font-size: 14px;">${assetName}</strong>
                ${locationInfo ? `<div style="font-size: 11px; color: #4b5563; margin-bottom: 6px;">${locationInfo}</div>` : ''}
                ${sourceLabel ? `<div style="font-size: 10px; color: ${sourceColor}; margin-bottom: 6px; font-weight: 600;">${sourceLabel}</div>` : ''}
                <div style="font-size: 11px; color: #16a34a; font-weight: 700; border-top: 1px solid #f1f5f9; padding-top: 6px; margin-top: 4px;">
                    <i class="fa fa-crosshairs"></i> ${lat.toFixed(6)}, ${lon.toFixed(6)}
                </div>
            </div>
        `;
    }

    updateMapPosition(lat, lon) {
        if (!this.map) return;

        console.log(`🗺️ [Asset Management] Updating map position to ${lat}, ${lon}`);
        this.map.setView([lat, lon], 15);

        if (this.marker) {
            this.marker.setLatLng([lat, lon]);
            this.marker.setPopupContent(this.getPopupContent(lat, lon));
        }
    }

    openInGoogleMaps() {
        if (this.hasValidCoordinates) {
            const url = `https://www.google.com/maps/search/?api=1&query=${this.latitude},${this.longitude}`;
            window.open(url, "_blank");
        }
    }

    openInOpenStreetMap() {
        if (this.hasValidCoordinates) {
            const url = `https://www.openstreetmap.org/?mlat=${this.latitude}&mlon=${this.longitude}&zoom=15`;
            window.open(url, "_blank");
        }
    }
}

// Register the widget
registry.category("fields").add("asset_map", {
    component: AssetMapWidget,
    supportedTypes: ["float", "char"],
});

console.log("🗺️ [Asset Management] AssetMapWidget widget registered");
