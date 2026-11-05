// Asset agent - Screen Time: writes the focused application (id + name only)
// to ~/.cache/asset-agent/focus.json for the agent to read once a minute.
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class AssetFocusExtension extends Extension {
    enable() {
        this._tracker = Shell.WindowTracker.get_default();
        this._path = GLib.build_filenamev([GLib.get_user_cache_dir(), 'asset-agent', 'focus.json']);
        GLib.mkdir_with_parents(GLib.path_get_dirname(this._path), 0o755);
        this._focusId = global.display.connect('notify::focus-window', () => this._write());
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, 30, () => {
            this._write();
            return GLib.SOURCE_CONTINUE;
        });
        this._write();
    }

    disable() {
        if (this._focusId)
            global.display.disconnect(this._focusId);
        if (this._timer)
            GLib.source_remove(this._timer);
        this._focusId = null;
        this._timer = null;
        this._tracker = null;
    }

    _write() {
        const win = global.display.focus_window;
        const app = win ? this._tracker.get_window_app(win) : null;
        // apps without a .desktop file come back as "window:N" / "gjs":
        // use the window class (e.g. "org.gnome.Extensions") instead
        const known = app && !app.get_id().startsWith('window:');
        const wmClass = win ? (win.get_wm_class() || win.get_wm_class_instance() || '') : '';
        const data = {
            app: known ? app.get_id() : wmClass,
            name: known ? app.get_name() : (wmClass || (app ? app.get_name() : '')),
            ts: Math.floor(Date.now() / 1000),
        };
        try {
            GLib.file_set_contents(this._path, JSON.stringify(data));
        } catch (e) {
            // cache dir not writable - nothing to report
        }
    }
}
