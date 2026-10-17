"""Check a file against its document type's AI rules BEFORE it is uploaded.

The upload dialog sends the chosen file here first. Nothing is stored on the DMS server:
the text is read in memory, the rules are checked, and the result is kept under the file's
SHA-256 fingerprint. The upload is then accepted only for that same file, and only if
every must-pass rule passed; its check result is copied to the new revision.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .dms_ai import MAX_TEXT_CHARS, _ocr_image, _ocr_pdf, _pdf_text, ai_json, check_text
from .dms_insights import find_sensitive, near_duplicates
from .dms_preview import DOC_EXT, IMAGE_CONVERT_EXT, IMAGE_EXT, SHEET_EXT, TEXT_EXT, _convert, is_password_protected


_TEXT_CACHE = {}   # sha256 -> text: the classify step and the rule check read the same file once
_TEXT_CACHE_MAX = 30


def cached_text(data, filename):
    key = hashlib.sha256(data).hexdigest()
    if key not in _TEXT_CACHE:
        if len(_TEXT_CACHE) >= _TEXT_CACHE_MAX:
            _TEXT_CACHE.pop(next(iter(_TEXT_CACHE)))
        _TEXT_CACHE[key] = extract_text(data, filename)
    return _TEXT_CACHE[key]


CLASSIFY_PROMPT = """You file documents in a company document management system.
Read the document text and choose its document type and folder from the lists given. Answer only with JSON:
{"type_code": "<one code from the type list>", "folder_id": <one id from the folder list>,
 "title": "<short title built from the content, not the file name, max 70 characters, e.g. Invoice INV-1048 - Sakthi Traders, NCR Report - Welding line, Leave Policy 2026>",
 "description": "<one sentence: what it is, who it is from or for, the date or period>",
 "fields": {<only the fields listed for the chosen type, with values copied from the text; null when absent>},
 "confidence": <0 to 1, how sure you are of the type>,
 "reason": "<few words on why this type and folder>"}
Prefer the folder whose recent documents look like this one. Never invent a code or id that is not in the lists."""


def extract_text(data, filename):
    """Readable text of a file that is not stored yet (OCR for scans and photos)."""
    ext = os.path.splitext(filename or '')[1].lstrip('.').lower()
    if not ext and data[:5] == b'%PDF-':
        ext = 'pdf'  # a PDF saved without its extension
    tmp = tempfile.mkdtemp(prefix='dms_pre_')
    try:
        text = ''
        if ext == 'pdf' or ext in DOC_EXT:
            pdf = data if ext == 'pdf' else _convert(data, ext, 'pdf', 'pdf')
            path = os.path.join(tmp, 'f.pdf')
            with open(path, 'wb') as f:
                f.write(pdf)
            text = _pdf_text(path)
            if len(text.strip()) < 40:
                text = _ocr_pdf(path)
        elif ext in SHEET_EXT:
            html = _convert(data, 'csv' if ext == 'tsv' else ext, 'html', 'html').decode('utf-8', 'replace')
            html = re.sub(r'<(style|script|head)\b.*?</\1>', ' ', html, flags=re.S | re.I)
            text = re.sub(r'<(td|th)[^>]*>', ' | ', html)
            text = re.sub(r'</tr>', '\n', text)
            text = re.sub(r'<[^>]+>', '', text)
        elif ext in IMAGE_EXT or ext in IMAGE_CONVERT_EXT:
            path = os.path.join(tmp, 'img.' + ext)
            with open(path, 'wb') as f:
                f.write(data)
            text = _ocr_image(path)
        elif ext in TEXT_EXT:
            text = data.decode('utf-8', 'replace')
            if ext in ('html', 'htm'):
                text = re.sub(r'<(script|style)\b.*?</\1>', ' ', text, flags=re.S | re.I)
                text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'[ \t]+', ' ', text or '')
        return re.sub(r'\n{3,}', '\n\n', text).strip()[:MAX_TEXT_CHARS]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class DmsAiPrecheck(models.Model):
    _name = 'dms.ai.precheck'
    _description = 'DMS AI check of a file before upload'
    _order = 'id desc'

    user_id = fields.Many2one('res.users', default=lambda s: s.env.user, required=True, index=True)
    type_id = fields.Many2one('dms.document.type', required=True, ondelete='cascade')
    sha256 = fields.Char(required=True, index=True)
    file_name = fields.Char()
    results = fields.Text()
    fields_json = fields.Text()
    summary = fields.Text()
    keywords = fields.Text()
    text_head = fields.Text()
    blocked = fields.Boolean()

    @api.model
    def run(self, type_id, title, filename, data):
        self._gc()
        dtype = self.env['dms.document.type'].browse(int(type_id)).exists()
        if not dtype or not dtype.ai_enabled:
            raise UserError(_("This document type has no AI check."))
        ext = os.path.splitext(filename or '')[1].lstrip('.').lower()
        if is_password_protected(data, ext):
            raise UserError(_("This file is password-protected, so the AI cannot read it. "
                              "Remove the password and choose the file again."))
        text = cached_text(data, filename)
        if not text:
            raise UserError(_("No readable text was found in this file. Choose a clearer copy."))
        out = check_text(self.env, dtype, title or filename, text)
        results = out['results']
        blocked = any(not x['passed'] and x['blocking'] for x in results)
        answer = out['answer']
        rec = self.sudo().create({
            'type_id': dtype.id, 'sha256': hashlib.sha256(data).hexdigest(), 'file_name': filename,
            'results': json.dumps(results), 'fields_json': json.dumps(out['fields']),
            'summary': answer.get('summary') or '', 'keywords': json.dumps(answer.get('keywords') or []),
            'text_head': text[:3000], 'blocked': blocked,
        })
        self.env['dms.log'].sudo().log('ai_check', details=_(
            "Checked before upload: %(f)s, %(p)s of %(n)s rules passed",
            f=filename, p=sum(x['passed'] for x in results), n=len(results)))
        return rec

    @api.model
    def classify(self, filename, data):
        """Suggest type, folder, title, description and fields for a file the user is about to upload."""
        user = self.env.user
        ext = os.path.splitext(filename or '')[1].lstrip('.').lower()
        if is_password_protected(data, ext):
            return {'ok': False, 'reason': _("Password-protected file: fill the form yourself.")}
        text = cached_text(data, filename)
        if len(text.strip()) < 20:
            return {'ok': False, 'reason': _("No readable text in this file: fill the form yourself.")}
        warnings = {'sensitive': find_sensitive(text), 'duplicates': self._visible_dups(near_duplicates(self.env, text))}
        types = self.env['dms.document.type'].search([])
        Folder = self.env['dms.folder']
        folders = Folder._visible_folders(user).filtered(
            lambda f: not f.is_archive and f._access_for_user(user) == 'edit')
        Doc = self.env['dms.document'].sudo()
        folder_lines = []
        for f in folders[:40]:
            recent = Doc.search([('folder_id', '=', f.id), ('state', '!=', 'discarded')], order='id desc', limit=3)
            seen = '; '.join('%s (%s)' % (d.name[:40], d.type_id.code) for d in recent)
            folder_lines.append('%s | %s%s' % (f.id, f.complete_name, (' | recent: ' + seen) if seen else ''))
        type_lines = ['%s | %s%s' % (t.code, t.name, (' | fields: ' + t.ai_fields) if t.ai_fields else '') for t in types]
        prompt = "TYPES (code | name | fields):\n%s\n\nFOLDERS (id | path | recent documents):\n%s\n\nFILE NAME: %s\n\nDOCUMENT TEXT:\n%s" % (
            '\n'.join(type_lines), '\n'.join(folder_lines), filename, text[:5000])
        try:
            ans = ai_json(self.env, CLASSIFY_PROMPT, prompt, max_tokens=600)
        except UserError as e:
            return {'ok': False, 'reason': str(e), **warnings}
        dtype = types.filtered(lambda t: t.code.upper() == str(ans.get('type_code') or '').upper())[:1]
        try:
            folder = folders.filtered(lambda f: f.id == int(ans.get('folder_id') or 0))[:1]
        except (TypeError, ValueError):
            folder = Folder
        try:
            confidence = max(0.0, min(1.0, float(ans.get('confidence') or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        found = ans.get('fields') if isinstance(ans.get('fields'), dict) else {}
        self.env['dms.log'].sudo().log('ai_check', folder=folder or None, details=_(
            "Upload form filled by AI: %(f)s → %(t)s", f=filename, t=dtype.name or '?'))
        return {
            'ok': True,
            'type_id': dtype.id or False, 'type': dtype.name or False,
            'folder_id': folder.id or False, 'folder': folder.complete_name or False,
            'title': str(ans.get('title') or '')[:120].strip(),
            'description': str(ans.get('description') or '')[:400].strip(),
            'fields': {k: v for k, v in found.items() if v not in (None, '', [])},
            'confidence': round(confidence, 2), 'reason': str(ans.get('reason') or '')[:200],
            **warnings,
        }

    @api.model
    def _visible_dups(self, dups):
        """Name only the documents this user may see; the others are counted."""
        user = self.env.user
        Doc = self.env['dms.document'].sudo()
        out, hidden = [], 0
        for x in dups:
            d = Doc.browse(x['doc_id'])
            if d._access_for_user(user) or user.has_group('dms.group_dms_controller'):
                out.append(x)
            else:
                hidden += 1
        if hidden:
            out.append({'hidden': hidden, 'same': False, 'score': 0})
        return out

    def payload(self):
        self.ensure_one()
        results = json.loads(self.results or '[]')
        return {
            'id': self.id, 'blocked': self.blocked, 'results': results,
            'fields': json.loads(self.fields_json or '{}'), 'summary': self.summary or '',
            'passed': sum(1 for x in results if x['passed']), 'total': len(results),
        }

    @api.model
    def _gc(self):
        """Pre-checks are only needed for a few minutes; drop those older than a day."""
        self.sudo().search([('create_date', '<', fields.Datetime.subtract(fields.Datetime.now(), days=1))]).unlink()

    def apply_to(self, rev):
        """Copy this check to a new revision (no second AI call)."""
        self.ensure_one()
        rev.sudo().write({
            'ai_state': 'done', 'ai_error': False, 'ai_results': self.results, 'ai_fields_json': self.fields_json,
            'ai_summary': self.summary, 'ai_blocked': self.blocked,
        })
        found = json.loads(self.fields_json or '{}')
        rev.document_id.sudo().ai_index = '\n'.join(filter(None, [
            ' '.join('%s: %s' % (k, v) for k, v in found.items() if v),
            self.summary or '', ' '.join(str(k) for k in json.loads(self.keywords or '[]')), self.text_head or '',
        ]))
