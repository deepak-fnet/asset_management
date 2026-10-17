/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

class SFTPFileManager extends Component {
    setup() {
        this.notification = useService("notification");

        this.uploadInputRef = useRef("uploadInput");
        this.fileListRef = useRef("fileList");

        this.state = useState({
            connected: false,
            serverName: '',
            currentPath: '/',
            homeDir: '/',
            files: [],
            selectedFiles: {},
            sortBy: 'name',
            sortAsc: true,
            loading: false,
            error: null,
            searchQuery: '',
            dragOver: false,
            showNewFolder: false,
            newFolderName: '',
            showRename: false,
            renameOldName: '',
            renameNewName: '',
            showDelete: false,
            deleteTarget: null,
            showEditor: false,
            editorPath: '',
            editorContent: '',
            editorDirty: false,
            editorSaving: false,
            transfers: [],
            transferCounter: 0,
            ctxMenu: null,
        });

        this.sessionId = null;
        this.accessRequestId = this.props.action.context?.access_request_id;
        this.accessToken = this.props.action.context?.access_token;

        onMounted(() => {
            if (!this.accessRequestId || !this.accessToken) {
                this.state.error = 'Invalid access. Please use the File Manager button from an approved request.';
                return;
            }
            this.connect();
            document.addEventListener('click', this._onDocumentClick);
        });

        onWillUnmount(() => {
            this.disconnect();
            document.removeEventListener('click', this._onDocumentClick);
        });
    }

    // ═══════════════════════════════════════════════════════════
    //  OWL 2 SAFE EVENT HANDLERS
    //  (no inline arrow functions — plain method names only)
    // ═══════════════════════════════════════════════════════════

    _onDocumentClick = () => {
        if (this.state.ctxMenu) this.state.ctxMenu = null;
    };

    // ── Dialog helpers ───────────────────────────────────────

    closeNewFolderDialog() { this.state.showNewFolder = false; }
    closeRenameDialog() { this.state.showRename = false; }
    closeDeleteDialog() { this.state.showDelete = false; }

    onNewFolderInput(ev) { this.state.newFolderName = ev.target.value; }
    onNewFolderKeydown(ev) { if (ev.key === 'Enter') this.createFolder(); }
    onRenameInput(ev) { this.state.renameNewName = ev.target.value; }
    onRenameKeydown(ev) { if (ev.key === 'Enter') this.doRename(); }

    // ── Breadcrumb ───────────────────────────────────────────

    onBreadcrumbClick(ev) {
        const path = ev.currentTarget.dataset.path;
        if (path) this.navigateTo(path);
    }

    // ── Sort columns ─────────────────────────────────────────

    sortByName() { this.sortByColumn('name'); }
    sortBySize() { this.sortByColumn('size'); }
    sortByMtime() { this.sortByColumn('mtime'); }

    // ── File row dispatchers (read index from data-index) ────

    _getFileFromEvent(ev) {
        const el = ev.currentTarget.closest('[data-index]') || ev.currentTarget;
        const idx = parseInt(el.dataset.index, 10);
        return this.getFilteredFiles()[idx];
    }

    onRowDblClick(ev) {
        const file = this._getFileFromEvent(ev);
        if (file) this.onFileDoubleClick(file);
    }

    onRowContextMenu(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const file = this._getFileFromEvent(ev);
        if (file) this.state.ctxMenu = { x: ev.clientX, y: ev.clientY, file };
    }

    onRowCheckbox(ev) {
        ev.stopPropagation();
        const file = this._getFileFromEvent(ev);
        if (file) this.toggleSelect(file);
    }

    onRowDownload(ev) {
        ev.stopPropagation();
        const file = this._getFileFromEvent(ev);
        if (file) this.downloadFile(file);
    }

    onRowEdit(ev) {
        ev.stopPropagation();
        const file = this._getFileFromEvent(ev);
        if (file) this.openEditor(file);
    }

    onRowRename(ev) {
        ev.stopPropagation();
        const file = this._getFileFromEvent(ev);
        if (file) this.onRenameClick(file);
    }

    onRowDelete(ev) {
        ev.stopPropagation();
        const file = this._getFileFromEvent(ev);
        if (file) this.onDeleteClick(file);
    }

    // ═══════════════════════════════════════════════════════════
    //  CONNECTION
    // ═══════════════════════════════════════════════════════════

    async connect() {
        this.state.loading = true;
        try {
            const res = await rpc('/ssh_terminal/sftp_connect', {
                access_request_id: this.accessRequestId,
                access_token: this.accessToken,
            });
            if (res.success) {
                this.sessionId = res.session_id;
                this.state.connected = true;
                this.state.serverName = res.server_name;
                this.state.homeDir = res.home_dir;
                this.state.currentPath = res.home_dir;
                await this.loadDir(res.home_dir);
            } else {
                this.state.error = res.error;
            }
        } catch (e) {
            this.state.error = e.message;
        }
        this.state.loading = false;
    }

    async disconnect() {
        if (!this.sessionId) return;
        try {
            await rpc('/ssh_terminal/sftp_close', { session_id: this.sessionId });
        } catch (e) { /* ignore */ }
        this.sessionId = null;
        this.state.connected = false;
    }

    async onDisconnect() {
        if (confirm('Disconnect from SFTP?')) {
            await this.disconnect();
            this.notification.add('SFTP session closed', { type: 'info', title: 'Disconnected' });
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  NAVIGATION
    // ═══════════════════════════════════════════════════════════

    async loadDir(path) {
        if (!this.sessionId) return;
        this.state.loading = true;
        this.state.selectedFiles = {};
        try {
            const res = await rpc('/ssh_terminal/sftp_list', {
                session_id: this.sessionId,
                path: path,
            });
            if (res.success) {
                this.state.currentPath = res.path;
                this.state.files = res.files;
                this.state.error = null;
            } else if (res.disconnect) {
                this.state.connected = false;
                this.state.error = res.error;
            } else {
                this.notification.add(res.error, { type: 'danger' });
            }
        } catch (e) {
            this.notification.add('Failed to load directory', { type: 'danger' });
        }
        this.state.loading = false;
    }

    async navigateTo(path) { await this.loadDir(path); }
    async goUp() { await this.loadDir(this.state.currentPath.replace(/\/[^/]+\/?$/, '') || '/'); }
    async goHome() { await this.loadDir(this.state.homeDir); }
    async refresh() { await this.loadDir(this.state.currentPath); }

    onFileDoubleClick(file) {
        if (file.is_dir) {
            this.navigateTo(this.state.currentPath.replace(/\/$/, '') + '/' + file.name);
        } else if (this.isTextFile(file.name) && file.size < 5 * 1024 * 1024) {
            this.openEditor(file);
        } else {
            this.downloadFile(file);
        }
    }

    getBreadcrumbs() {
        const parts = this.state.currentPath.split('/').filter(Boolean);
        const crumbs = [{ name: '/', path: '/' }];
        let acc = '';
        for (const p of parts) { acc += '/' + p; crumbs.push({ name: p, path: acc }); }
        return crumbs;
    }

    // ═══════════════════════════════════════════════════════════
    //  FILE OPERATIONS
    // ═══════════════════════════════════════════════════════════

    async downloadFile(file) {
        // FIX: Use fetch + Blob URL so Odoo's router cannot intercept the download.
        // A plain <a>.click() on an internal URL gets caught by Odoo's client-side
        // router and never reaches the browser's download handler.
        const path = this.state.currentPath.replace(/\/$/, '') + '/' + file.name;
        const url = `/ssh_terminal/sftp_download?session_id=${encodeURIComponent(this.sessionId)}&path=${encodeURIComponent(path)}`;
        const tid = this.addTransfer('download', file.name, file.size, 'progress');
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
            const blob = await resp.blob();
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            a.download = file.name;
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            // Clean up after the browser has had time to initiate the download
            setTimeout(() => {
                document.body.removeChild(a);
                URL.revokeObjectURL(blobUrl);
            }, 2000);
            this.updateTransfer(tid, 'success');
        } catch (e) {
            this.updateTransfer(tid, 'error', e.message);
            this.notification.add('Download failed: ' + e.message, { type: 'danger' });
        }
    }

    onUploadClick() { this.uploadInputRef.el.value = ''; this.uploadInputRef.el.click(); }

    async onFileSelected(ev) {
        if (ev.target.files.length) await this.uploadFiles(ev.target.files);
    }

    async uploadFiles(fileList) {
        for (const file of fileList) {
            const tid = this.addTransfer('upload', file.name, file.size, 'progress');
            try {
                const b64 = await this.readFileAsBase64(file);
                const res = await rpc('/ssh_terminal/sftp_upload', {
                    session_id: this.sessionId,
                    path: this.state.currentPath,
                    data_b64: b64,
                    filename: file.name,
                });
                if (res.success) { this.updateTransfer(tid, 'success'); }
                else { this.updateTransfer(tid, 'error', res.error); this.notification.add(res.error, { type: 'danger' }); }
            } catch (e) { this.updateTransfer(tid, 'error', e.message); }
        }
        await this.refresh();
    }

    readFileAsBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.onerror = () => reject(new Error('Read failed'));
            reader.readAsDataURL(file);
        });
    }

    // ── New Folder ───────────────────────────────────────────

    onNewFolder() { this.state.newFolderName = ''; this.state.showNewFolder = true; }

    async createFolder() {
        const name = this.state.newFolderName.trim();
        if (!name) return;
        try {
            const res = await rpc('/ssh_terminal/sftp_mkdir', {
                session_id: this.sessionId,
                path: this.state.currentPath.replace(/\/$/, '') + '/' + name,
            });
            if (res.success) {
                this.state.showNewFolder = false;
                await this.refresh();
                this.notification.add(`Folder "${name}" created`, { type: 'success' });
            } else { this.notification.add(res.error, { type: 'danger' }); }
        } catch (e) { this.notification.add(e.message, { type: 'danger' }); }
    }

    // ── Rename ───────────────────────────────────────────────

    onRenameClick(file) {
        this.state.renameOldName = file.name;
        this.state.renameNewName = file.name;
        this.state.showRename = true;
    }

    async doRename() {
        const old = this.state.renameOldName;
        const nw = this.state.renameNewName.trim();
        if (!nw || nw === old) { this.state.showRename = false; return; }
        try {
            const base = this.state.currentPath.replace(/\/$/, '');
            const res = await rpc('/ssh_terminal/sftp_rename', {
                session_id: this.sessionId, old_path: base + '/' + old, new_path: base + '/' + nw,
            });
            if (res.success) { this.state.showRename = false; await this.refresh(); }
            else { this.notification.add(res.error, { type: 'danger' }); }
        } catch (e) { this.notification.add(e.message, { type: 'danger' }); }
    }

    // ── Delete ───────────────────────────────────────────────

    onDeleteClick(file) { this.state.deleteTarget = file; this.state.showDelete = true; }

    onDeleteSelected() {
        const names = Object.keys(this.state.selectedFiles);
        if (!names.length) return;
        this.state.deleteTarget = { name: `${names.length} selected items`, _multi: names };
        this.state.showDelete = true;
    }

    async doDelete() {
        const target = this.state.deleteTarget;
        if (!target) return;
        const base = this.state.currentPath.replace(/\/$/, '');
        if (target._multi) {
            for (const name of target._multi) {
                const file = this.state.files.find(f => f.name === name);
                await rpc('/ssh_terminal/sftp_delete', {
                    session_id: this.sessionId, path: base + '/' + name, is_dir: file ? file.is_dir : false,
                });
            }
        } else {
            await rpc('/ssh_terminal/sftp_delete', {
                session_id: this.sessionId, path: base + '/' + target.name, is_dir: target.is_dir || false,
            });
        }
        this.state.showDelete = false;
        this.state.deleteTarget = null;
        this.state.selectedFiles = {};
        await this.refresh();
        this.notification.add('Deleted successfully', { type: 'success' });
    }

    // ═══════════════════════════════════════════════════════════
    //  INLINE EDITOR
    // ═══════════════════════════════════════════════════════════

    async openEditor(file) {
        const path = this.state.currentPath.replace(/\/$/, '') + '/' + file.name;
        this.state.editorPath = path;
        this.state.editorContent = '';
        this.state.editorDirty = false;
        this.state.showEditor = true;
        this.state.editorSaving = false;
        try {
            const res = await rpc('/ssh_terminal/sftp_read_file', { session_id: this.sessionId, path });
            if (res.success) { this.state.editorContent = res.content; }
            else { this.notification.add(res.error, { type: 'danger' }); this.state.showEditor = false; }
        } catch (e) { this.notification.add('Failed to open file', { type: 'danger' }); this.state.showEditor = false; }
    }

    onEditorChange(ev) { this.state.editorContent = ev.target.value; this.state.editorDirty = true; }

    async saveEditorFile() {
        this.state.editorSaving = true;
        try {
            const res = await rpc('/ssh_terminal/sftp_write_file', {
                session_id: this.sessionId, path: this.state.editorPath, content: this.state.editorContent,
            });
            if (res.success) { this.state.editorDirty = false; this.notification.add('File saved', { type: 'success' }); }
            else { this.notification.add(res.error, { type: 'danger' }); }
        } catch (e) { this.notification.add('Save failed', { type: 'danger' }); }
        this.state.editorSaving = false;
    }

    closeEditor() {
        if (this.state.editorDirty && !confirm('Unsaved changes will be lost. Close anyway?')) return;
        this.state.showEditor = false;
    }

    // ═══════════════════════════════════════════════════════════
    //  DRAG & DROP
    // ═══════════════════════════════════════════════════════════

    onDragOver(ev) { ev.preventDefault(); ev.stopPropagation(); this.state.dragOver = true; }
    onDragLeave(ev) { ev.preventDefault(); this.state.dragOver = false; }
    async onDrop(ev) {
        ev.preventDefault(); ev.stopPropagation(); this.state.dragOver = false;
        if (ev.dataTransfer && ev.dataTransfer.files.length) await this.uploadFiles(ev.dataTransfer.files);
    }

    // ═══════════════════════════════════════════════════════════
    //  CONTEXT MENU
    // ═══════════════════════════════════════════════════════════

    ctxDownload() { if (this.state.ctxMenu?.file) this.downloadFile(this.state.ctxMenu.file); this.state.ctxMenu = null; }
    ctxRename() { if (this.state.ctxMenu?.file) this.onRenameClick(this.state.ctxMenu.file); this.state.ctxMenu = null; }
    ctxDelete() { if (this.state.ctxMenu?.file) this.onDeleteClick(this.state.ctxMenu.file); this.state.ctxMenu = null; }
    ctxEdit() { if (this.state.ctxMenu?.file) this.openEditor(this.state.ctxMenu.file); this.state.ctxMenu = null; }
    ctxOpen() {
        if (this.state.ctxMenu?.file?.is_dir) {
            this.navigateTo(this.state.currentPath.replace(/\/$/, '') + '/' + this.state.ctxMenu.file.name);
        }
        this.state.ctxMenu = null;
    }

    // ═══════════════════════════════════════════════════════════
    //  SELECTION & SORTING
    // ═══════════════════════════════════════════════════════════

    toggleSelect(file) {
        if (this.state.selectedFiles[file.name]) delete this.state.selectedFiles[file.name];
        else this.state.selectedFiles[file.name] = true;
        this.state.selectedFiles = { ...this.state.selectedFiles };
    }

    toggleSelectAll() {
        const visible = this.getFilteredFiles();
        const allSelected = visible.length > 0 && visible.every(f => this.state.selectedFiles[f.name]);
        if (allSelected) { this.state.selectedFiles = {}; }
        else { const sel = {}; visible.forEach(f => sel[f.name] = true); this.state.selectedFiles = sel; }
    }

    get selectedCount() { return Object.keys(this.state.selectedFiles).length; }

    sortByColumn(col) {
        if (this.state.sortBy === col) this.state.sortAsc = !this.state.sortAsc;
        else { this.state.sortBy = col; this.state.sortAsc = true; }
    }

    getSortIcon(col) {
        if (this.state.sortBy !== col) return 'fa fa-sort text-muted';
        return this.state.sortAsc ? 'fa fa-sort-asc' : 'fa fa-sort-desc';
    }

    onSearchInput(ev) { this.state.searchQuery = ev.target.value; }

    getFilteredFiles() {
        let files = [...this.state.files];
        const q = this.state.searchQuery.toLowerCase();
        if (q) files = files.filter(f => f.name.toLowerCase().includes(q));
        const col = this.state.sortBy;
        const dir = this.state.sortAsc ? 1 : -1;
        files.sort((a, b) => {
            if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
            if (col === 'name') return a.name.localeCompare(b.name) * dir;
            if (col === 'size') return (a.size - b.size) * dir;
            if (col === 'mtime') return (a.mtime - b.mtime) * dir;
            return 0;
        });
        return files;
    }

    // ═══════════════════════════════════════════════════════════
    //  TRANSFER LOG
    // ═══════════════════════════════════════════════════════════

    addTransfer(type, name, size, status) {
        const id = ++this.state.transferCounter;
        this.state.transfers.unshift({ id, type, name, size: this.formatSize(size), status, error: '', time: new Date().toLocaleTimeString() });
        if (this.state.transfers.length > 50) this.state.transfers.pop();
        return id;
    }
    updateTransfer(id, status, error = '') {
        const t = this.state.transfers.find(x => x.id === id);
        if (t) { t.status = status; t.error = error; }
    }
    clearTransfers() { this.state.transfers = []; }

    // ═══════════════════════════════════════════════════════════
    //  HELPERS
    // ═══════════════════════════════════════════════════════════

    formatSize(bytes) {
        if (!bytes || bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
    }

    formatDate(ts) {
        if (!ts) return '—';
        const d = new Date(ts * 1000);
        const opts = { month: 'short', day: 'numeric' };
        if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
        return d.toLocaleDateString(undefined, opts) + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    }

    getFileIcon(file) {
        if (file.is_dir) return 'fa fa-folder sftp-icon-folder';
        if (file.is_link) return 'fa fa-share sftp-icon-link';
        const ext = file.name.split('.').pop().toLowerCase();
        const map = {
            py: 'fa fa-file-code-o sftp-icon-code', js: 'fa fa-file-code-o sftp-icon-code',
            ts: 'fa fa-file-code-o sftp-icon-code', jsx: 'fa fa-file-code-o sftp-icon-code',
            css: 'fa fa-file-code-o sftp-icon-code', html: 'fa fa-file-code-o sftp-icon-code',
            xml: 'fa fa-file-code-o sftp-icon-code', json: 'fa fa-file-code-o sftp-icon-code',
            yml: 'fa fa-file-code-o sftp-icon-code', yaml: 'fa fa-file-code-o sftp-icon-code',
            sh: 'fa fa-terminal sftp-icon-code', bash: 'fa fa-terminal sftp-icon-code',
            conf: 'fa fa-cog sftp-icon-config', cfg: 'fa fa-cog sftp-icon-config',
            ini: 'fa fa-cog sftp-icon-config', env: 'fa fa-cog sftp-icon-config',
            png: 'fa fa-file-image-o sftp-icon-image', jpg: 'fa fa-file-image-o sftp-icon-image',
            jpeg: 'fa fa-file-image-o sftp-icon-image', gif: 'fa fa-file-image-o sftp-icon-image',
            svg: 'fa fa-file-image-o sftp-icon-image', webp: 'fa fa-file-image-o sftp-icon-image',
            pdf: 'fa fa-file-pdf-o sftp-icon-pdf',
            zip: 'fa fa-file-archive-o sftp-icon-archive', tar: 'fa fa-file-archive-o sftp-icon-archive',
            gz: 'fa fa-file-archive-o sftp-icon-archive', bz2: 'fa fa-file-archive-o sftp-icon-archive',
            txt: 'fa fa-file-text-o sftp-icon-text', md: 'fa fa-file-text-o sftp-icon-text',
            log: 'fa fa-file-text-o sftp-icon-text', csv: 'fa fa-file-text-o sftp-icon-text',
        };
        return map[ext] || 'fa fa-file-o sftp-icon-default';
    }

    isTextFile(name) {
        const ext = name.split('.').pop().toLowerCase();
        return ['txt','md','log','csv','json','xml','html','css','js','ts','py','rb','sh','bash',
                'yml','yaml','conf','cfg','ini','env','toml','sql','php','java','c','cpp','h',
                'rs','go','jsx','tsx','vue','svelte','dockerfile'].includes(ext) || name.startsWith('.');
    }

    getEditorFilename() { return this.state.editorPath.split('/').pop(); }
}

SFTPFileManager.template = "ssh_terminal.SFTPFileManager";
SFTPFileManager.props = ["*"];
registry.category("actions").add("ssh_sftp_widget", SFTPFileManager);