"""AI checks for uploaded documents (OpenAI-compatible server, e.g. vLLM).

On upload / check-in the file's text is read (pdftotext, LibreOffice, or OCR for scans),
then the AI checks it against the rules of its document type, pulls out the fields the
type asks for, and writes a short summary. A failed blocking rule keeps the revision
from going to approval until a corrected file is uploaded.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading

import requests

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 11000      # the server has an 8k-token context; keep room for rules and answer
AI_TIMEOUT = 120


# ---------------------------------------------------------------------- AI client
def _ai_params(env):
    ICP = env['ir.config_parameter'].sudo()
    return {
        'enabled': ICP.get_param('dms.ai_enabled', 'True') not in ('False', '0', ''),
        'url': (ICP.get_param('dms.ai_url') or 'http://103.127.189.70:8000/v1').rstrip('/'),
        'model': ICP.get_param('dms.ai_model') or 'Qwen/Qwen3-32B-AWQ',
        'key': ICP.get_param('dms.ai_key') or '',
    }


def ai_json(env, system, user, max_tokens=900):
    """Ask the AI and return its JSON answer as a dict."""
    p = _ai_params(env)
    headers = {'Content-Type': 'application/json'}
    if p['key']:
        headers['Authorization'] = 'Bearer %s' % p['key']
    payload = {
        'model': p['model'],
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'temperature': 0,
        'max_tokens': max_tokens,
        'response_format': {'type': 'json_object'},
        'chat_template_kwargs': {'enable_thinking': False},  # Qwen3: answer directly
    }
    try:
        r = requests.post(p['url'] + '/chat/completions', json=payload, headers=headers, timeout=AI_TIMEOUT)
        r.raise_for_status()
        content = r.json()['choices'][0]['message']['content'] or ''
    except requests.RequestException as e:
        raise UserError(_("The AI server did not answer: %s", e))
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.S).strip()
    m = re.search(r'\{.*\}', content, re.S)
    try:
        return json.loads(m.group(0) if m else content)
    except (ValueError, AttributeError):
        raise UserError(_("The AI answer was not readable."))


# ---------------------------------------------------------------------- text extraction
def _pdf_text(path):
    out = subprocess.run(['pdftotext', '-layout', '-l', '6', path, '-'], capture_output=True, timeout=60)
    return out.stdout.decode('utf-8', 'replace')


def _ocr_pdf(path):
    tmp = tempfile.mkdtemp(prefix='dms_ocr_')
    try:
        subprocess.run(['pdftoppm', '-r', '200', '-l', '3', '-png', path, os.path.join(tmp, 'p')],
                       capture_output=True, timeout=120)
        text = []
        for f in sorted(os.listdir(tmp)):
            if f.endswith('.png'):
                text.append(_ocr_image(os.path.join(tmp, f)))
        return '\n'.join(text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ocr_image(path):
    if not shutil.which('tesseract'):
        return ''
    out = subprocess.run(['tesseract', path, '-', '-l', 'eng'], capture_output=True, timeout=120)
    return out.stdout.decode('utf-8', 'replace')


# ---------------------------------------------------------------------- models
def check_text(env, dtype, title, text, exclude_doc_id=None, exclude_rev_id=None):
    """Run the type's rules on a document text. Returns {'results', 'fields', 'answer'}."""
    all_rules = dtype.ai_rule_ids.filtered('active')
    rules = all_rules.filtered(lambda r: r.kind == 'ai')
    calc_rules = all_rules.filtered(lambda r: r.kind == 'calculation')
    dup_rules = all_rules.filtered(lambda r: r.kind == 'duplicate')
    wanted = [f.strip() for f in (dtype.ai_fields or '').split(',') if f.strip()]
    conditional = re.compile(r'where applicable|if required|where required|if the vendor is|if registered', re.I)
    system = ("You are a careful document checker for a document management system. "
              "Read the document text and answer ONLY with JSON. Judge each rule strictly from the text; "
              "if something cannot be found in the text, the rule fails. Reasons must be short, specific, "
              "and say what is missing or wrong so the user can fix the file. "
              "For any rule about amounts, totals or numbers: first copy the numbers from the text, then do the "
              "arithmetic yourself digit by digit, and write the calculation in the reason "
              "(for example '1,00,000 + 18,000 = 1,18,000, but the invoice total is 1,25,000'). "
              "A rule passes only if your own calculation matches the document exactly. "
              "Indian number format: 1,00,000 = 100000. "
              "Rules that say 'where applicable', 'if required' or 'if registered' pass when they do not apply to "
              "this document; say why in the reason. A legitimately exempt or non-taxable line does not fail a tax rule. "
              "Keep every reason under 30 words.")
    user = (
        "Document type: %s\nTitle: %s\n\nRules:\n%s\n\nFields to extract: %s\n\n"
        "Return JSON: {\"results\": [{\"rule\": <rule number>, \"passed\": true/false, \"reason\": \"...\"}], "
        "\"fields\": {<field>: <value or null>}, \"summary\": \"one or two sentences\", "
        "\"keywords\": [\"words people may search for, including names and numbers\"]%s}\n\n"
        "Document text:\n\"\"\"\n%s\n\"\"\"" % (
            dtype.name, title,
            '\n'.join('%d. %s%s' % (i + 1, r.name, ' [mark passed if this does not apply to this document]'
                                     if conditional.search(r.name) else '') for i, r in enumerate(rules)) or '(none)',
            ', '.join(wanted) or '(none)',
            (', "amounts": {"line_amounts": [each line\'s value before tax, as numbers], '
             '"taxes": [each tax amount, as numbers], "discounts": [each discount, as numbers], '
             '"charges": [freight, handling and other charges, as numbers], "rounding": number or 0, '
             '"total": the total payable, as a number}') if calc_rules else '',
            text)
    )
    answer = ai_json(env, system, user, max_tokens=2600)
    results = []
    by_no = {}
    for item in answer.get('results') or []:
        try:
            by_no[int(item.get('rule'))] = item
        except (TypeError, ValueError):
            continue
    for i, r in enumerate(rules):
        item = by_no.get(i + 1, {})
        passed = bool(item.get('passed'))
        results.append({'rule': r.name, 'passed': passed, 'blocking': r.blocking,
                        'reason': item.get('reason') or (_("Not checked") if not item else ''),
                        'hint': r.hint or ''})
    found = answer.get('fields') or {}
    if wanted:
        found = {k: found.get(k) for k in wanted}
    for r in calc_rules:
        results.append(calculation_result(r, answer.get('amounts') or {}))
    for r in dup_rules:
        results.append(duplicate_result(env, dtype, r, found, exclude_doc_id, exclude_rev_id))
    return {'results': results, 'fields': found, 'answer': answer}


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(re.sub(r'[^\d.\-]', '', str(v or '')) or 0)
    except ValueError:
        return 0.0


def _money(v):
    """Indian grouping: 118000 -> 1,18,000.00"""
    sign = '-' if v < 0 else ''
    whole, frac = ('%.2f' % abs(v)).split('.')
    head, tail = whole[:-3], whole[-3:]
    if head:
        head = ','.join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        whole = head + ',' + tail
    return '%s%s.%s' % (sign, whole, frac)


def calculation_result(rule, amounts):
    """Add up the numbers the AI read and compare with the total (the AI does not do the sum)."""
    res = {'rule': rule.name, 'blocking': rule.blocking, 'hint': rule.hint or '', 'passed': False}
    lines = [_num(x) for x in amounts.get('line_amounts') or []]
    taxes = [_num(x) for x in amounts.get('taxes') or []]
    discounts = [abs(_num(x)) for x in amounts.get('discounts') or []]
    charges = [_num(x) for x in amounts.get('charges') or []]
    rounding = _num(amounts.get('rounding'))
    total = _num(amounts.get('total'))
    if not lines or not total:
        res['reason'] = _("Could not read the line amounts and the total from the file.")
        return res
    calc = sum(lines) + sum(taxes) + sum(charges) - sum(discounts) + rounding
    parts = ['lines %s' % _money(sum(lines))]
    if taxes:
        parts.append('+ tax %s' % _money(sum(taxes)))
    if charges:
        parts.append('+ charges %s' % _money(sum(charges)))
    if discounts:
        parts.append('- discount %s' % _money(sum(discounts)))
    if rounding:
        parts.append('+ rounding %s' % _money(rounding))
    working = '%s = %s' % (' '.join(parts), _money(calc))
    if abs(calc - total) <= 1.0:
        res['passed'] = True
        res['reason'] = _("%(w)s, matches the total %(t)s.", w=working, t=_money(total))
    else:
        res['reason'] = _("%(w)s, but the invoice total is %(t)s (difference %(d)s).",
                          w=working, t=_money(total), d=_money(total - calc))
    return res


def duplicate_result(env, dtype, rule, found, exclude_doc_id=None, exclude_rev_id=None):
    """Compare extracted fields with every other live document of the same type."""
    keys = [k.strip() for k in (rule.match_fields or '').split(',') if k.strip()]
    mine = {k: _norm(found.get(k)) for k in keys}
    res = {'rule': rule.name, 'blocking': rule.blocking, 'hint': rule.hint or '', 'passed': True}
    if sum(1 for v in mine.values() if v) < 2:
        res['reason'] = _("Not enough details found to compare (%s).", ', '.join(keys))
        return res
    domain = [('document_id.type_id', '=', dtype.id), ('ai_fields_json', '!=', False),
              ('state', 'not in', ('dropped', 'rejected', 'disposed'))]
    if exclude_rev_id:
        domain.append(('id', '!=', exclude_rev_id))
    if exclude_doc_id:
        domain.append(('document_id', '!=', exclude_doc_id))
    for other in env['dms.revision'].sudo().search(domain):
        try:
            theirs = json.loads(other.ai_fields_json or '{}')
        except ValueError:
            continue
        shared = [k for k in keys if mine[k] and _norm(theirs.get(k))]
        if len(shared) >= 2 and all(mine[k] == _norm(theirs.get(k)) for k in shared):
            res['passed'] = False
            res['reason'] = _("Same %(f)s as %(doc)s.", f=', '.join(x.replace('_', ' ') for x in shared),
                              doc=other.document_id.display_name)
            return res
    res['reason'] = _("No other document has the same %s.", ', '.join(k.replace('_', ' ') for k in keys))
    return res



class DmsLogAi(models.Model):
    _inherit = 'dms.log'

    action = fields.Selection(selection_add=[('ai_check', 'AI Check'), ('ai_search', 'AI Search')],
                              ondelete={'ai_check': 'cascade', 'ai_search': 'cascade'})


class DmsAiRule(models.Model):
    _name = 'dms.ai.rule'
    _description = 'DMS AI Upload Rule'
    _order = 'sequence, id'

    type_id = fields.Many2one('dms.document.type', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char("Rule", required=True, help="Plain English, e.g. 'The invoice shows the vendor GST number'.")
    hint = fields.Char("What to tell the user when it fails", help="Optional: how to fix it.")
    blocking = fields.Boolean("Must pass", default=True,
                              help="If it fails, the document cannot be sent for approval until a corrected file is uploaded.")
    active = fields.Boolean(default=True)
    kind = fields.Selection([('ai', 'AI reads the file'),
                             ('calculation', 'Total calculation (AI reads the numbers, the system adds them up)'),
                             ('duplicate', 'Duplicate check (compares with saved documents)')],
                            default='ai', required=True,
                            help="Duplicate check: the extracted fields are compared with the documents already in the DMS.")
    match_fields = fields.Char("Fields to compare", default='vendor_gstin, invoice_number, invoice_date, total_amount',
                               help="Extracted fields used by a duplicate check. Each must be listed in the type's "
                                    "'Fields to extract'. A document is a duplicate when every field both documents have matches, "
                                    "and at least two fields could be compared.")


def _norm(value):
    """Compare extracted values loosely: case, spaces, commas, currency marks and date separators."""
    v = str(value or '').strip().upper()
    if not v or v in ('NULL', 'NONE', 'N/A', '-'):
        return ''
    v = re.sub(r'(RS\.?|INR|₹)', '', v)
    if re.fullmatch(r'[\d\s,.]+', v):  # an amount: 1,18,000.00 == 118000
        try:
            return '%.2f' % float(v.replace(',', '').replace(' ', ''))
        except ValueError:
            pass
    d = re.fullmatch(r'(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2,4})', v)
    if d:  # a date: 12-10-2026 == 12/10/2026
        day, month, year = d.groups()
        return '%02d-%02d-%s' % (int(day), int(month), year if len(year) == 4 else '20' + year)
    return re.sub(r'[\s\-/.,]', '', v)


class DmsDocumentTypeAi(models.Model):
    _inherit = 'dms.document.type'

    ai_enabled = fields.Boolean("AI check on upload", default=False)
    ai_rule_ids = fields.One2many('dms.ai.rule', 'type_id', "Upload rules")
    ai_fields = fields.Char(
        "Fields to extract", help="Comma separated, e.g. vendor, invoice_number, invoice_date, amount, gst_number")


class DmsDocumentAi(models.Model):
    _inherit = 'dms.document'

    @api.model
    def upload_new(self, folder_id, type_id, title, description, filename, data, submit=False, precheck_id=None):
        """For an AI-checked type the file must have passed the check before it is stored."""
        dtype = self.env['dms.document.type'].browse(int(type_id))
        if not (dtype.ai_enabled and _ai_params(self.env)['enabled']):
            return super().upload_new(folder_id, type_id, title, description, filename, data, submit=submit)
        import hashlib
        pre = self.env['dms.ai.precheck'].sudo().browse(int(precheck_id or 0)).exists()
        if not pre or pre.user_id != self.env.user or pre.type_id != dtype or pre.sha256 != hashlib.sha256(data).hexdigest():
            raise UserError(_("Check this file with the AI before uploading it."))
        if pre.blocked:
            raise UserError(_("This file did not pass the required rules. Choose a corrected file and check it again."))
        doc = super(DmsDocumentAi, self.with_context(dms_ai_prechecked=True)).upload_new(
            folder_id, type_id, title, description, filename, data, submit=False)
        rev = doc.revision_ids[:1]
        pre.apply_to(rev)
        if submit:
            rev.with_user(self.env.user).action_submit()
        return doc

    ai_index = fields.Text("AI search text", readonly=True,
                           help="Extracted fields, summary and the start of the text, used by search.")


class DmsRevisionAi(models.Model):
    _inherit = 'dms.revision'

    ai_state = fields.Selection([('pending', 'Checking'), ('done', 'Checked'), ('error', 'Not checked')],
                                readonly=True, copy=False)
    ai_results = fields.Text(readonly=True, copy=False)       # JSON list of {rule, passed, reason, blocking, hint}
    ai_fields_json = fields.Text(readonly=True, copy=False)   # JSON dict
    ai_summary = fields.Text(readonly=True, copy=False)
    ai_error = fields.Char(readonly=True, copy=False)
    ai_blocked = fields.Boolean(readonly=True, copy=False, help="A must-pass rule failed.")
    ai_submit_after = fields.Boolean(readonly=True, copy=False, help="Send for approval as soon as the check passes.")

    # ------------------------------------------------------------------ text
    def _ai_text(self):
        """Readable text of the file, OCR included for scans and photos."""
        self.ensure_one()
        kind = self._preview_kind()
        text = ''
        try:
            if kind in ('pdf', 'doc'):
                path = self._pdf_path()
                text = _pdf_text(path)
                if len(text.strip()) < 40:  # a scan: no text layer
                    text = _ocr_pdf(path)
            elif kind == 'sheet':
                html = self._sheet_html().decode('utf-8', 'replace')
                html = re.sub(r'<(style|script|head)\b.*?</\1>', ' ', html, flags=re.S | re.I)
                text = re.sub(r'<(td|th)[^>]*>', ' | ', html)
                text = re.sub(r'</tr>', '\n', text)
                text = re.sub(r'<[^>]+>', '', text)
                text = re.sub(r'[ \t]+', ' ', text)
            elif kind in ('image', 'image_convert'):
                tmp = tempfile.mkdtemp(prefix='dms_ocr_')
                try:
                    p = os.path.join(tmp, 'img.' + (self._ext() or 'png'))
                    with open(p, 'wb') as f:
                        f.write(self._read_data())
                    text = _ocr_image(p)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            elif kind == 'text':
                text = self._text()
                if self._ext() in ('html', 'htm', 'xhtml'):
                    text = re.sub(r'<(script|style)\b.*?</\1>', ' ', text, flags=re.S | re.I)
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'&nbsp;', ' ', text)
        except Exception as e:  # noqa: BLE001
            _logger.info("DMS AI: no text for revision %s: %s", self.id, e)
        text = re.sub(r'\n{3,}', '\n\n', text or '').strip()
        return text[:MAX_TEXT_CHARS]

    # ------------------------------------------------------------------ check
    def _ai_check(self):
        self.ensure_one()
        doc = self.document_id
        dtype = doc.type_id
        all_rules = dtype.ai_rule_ids.filtered('active')
        rules = all_rules.filtered(lambda r: r.kind == 'ai')
        dup_rules = all_rules - rules
        from .dms_preview import is_password_protected
        if self._preview_kind() in ('pdf', 'doc', 'sheet') and is_password_protected(self._read_data(), self._ext()):
            self.write({'ai_state': 'error', 'ai_blocked': bool(rules.filtered('blocking')),
                        'ai_error': _("The file is password-protected, so the AI cannot read it. "
                                      "Upload a copy without a password to have it checked.")})
            return
        text = self._ai_text()
        if not text:
            self.write({'ai_state': 'error', 'ai_error': _("No readable text in this file."),
                        'ai_blocked': bool(rules.filtered('blocking'))})
            return
        out = check_text(self.env, dtype, doc.name, text, exclude_doc_id=doc.id, exclude_rev_id=self.id)
        results, found, answer = out['results'], out['fields'], out['answer']
        blocked = any(not x['passed'] and x['blocking'] for x in results)
        self.write({
            'ai_state': 'done', 'ai_error': False,
            'ai_results': json.dumps(results), 'ai_fields_json': json.dumps(found),
            'ai_summary': answer.get('summary') or '', 'ai_blocked': blocked,
        })
        keywords = answer.get('keywords') or []
        doc.sudo().ai_index = '\n'.join(filter(None, [
            ' '.join('%s: %s' % (k, v) for k, v in found.items() if v),
            answer.get('summary') or '',
            ' '.join(str(k) for k in keywords),
            text[:3000],
        ]))
        self.env['dms.log'].sudo().log('ai_check', revision=self, details=_(
            "AI check: %(p)s of %(n)s rules passed", p=sum(x['passed'] for x in results), n=len(results)))

    def _ai_run_and_follow_up(self):
        """Check, then send for approval if the user asked for it and nothing blocks."""
        self.ensure_one()
        try:
            self._ai_check()
        except Exception as e:  # noqa: BLE001
            self.write({'ai_state': 'error', 'ai_error': str(e)[:250]})
            _logger.warning("DMS AI check failed for revision %s: %s", self.id, e)
        if self.ai_submit_after and self.state == 'draft' and not self.ai_blocked and self.ai_state == 'done':
            self.ai_submit_after = False
            self.with_user(self.author_id).action_submit()
        doc = self.document_id
        if self.ai_blocked and self.author_id.partner_id:
            doc.message_post(body=_("AI check: %s needs a corrected file before it can be sent for approval.",
                                    self.display_name),
                             subtype_xmlid='mail.mt_note')

    def _schedule_ai(self):
        todo = self.filtered(lambda r: r.document_id.type_id.ai_enabled)
        if not todo or not _ai_params(self.env)['enabled']:
            return
        todo.write({'ai_state': 'pending', 'ai_error': False})
        dbname, ids = self.env.cr.dbname, todo.ids

        def run():
            for rid in ids:
                try:
                    with Registry(dbname).cursor() as cr:
                        env = api.Environment(cr, SUPERUSER_ID, {})
                        rev = env['dms.revision'].browse(rid).exists()
                        if rev:
                            rev._ai_run_and_follow_up()
                except Exception:  # noqa: BLE001
                    _logger.exception("DMS AI thread failed for revision %s", rid)

        self.env.cr.postcommit.add(lambda: threading.Thread(target=run, name='dms-ai', daemon=True).start())

    @api.model
    def _create_with_file(self, doc, filename, data, note):
        rev = super()._create_with_file(doc, filename, data, note)
        if not self.env.context.get('dms_ai_prechecked'):
            rev._schedule_ai()
        if not doc.type_id.ai_enabled:
            rev._schedule_index()
        return rev

    def _index_text(self):
        """Make the words inside the file searchable (no AI call, just the file's text)."""
        self.ensure_one()
        text = self._ai_text()
        if text:
            self.document_id.sudo().ai_index = text[:8000]

    def _schedule_index(self):
        dbname, ids = self.env.cr.dbname, self.ids

        def run():
            for rid in ids:
                try:
                    with Registry(dbname).cursor() as cr:
                        rev = api.Environment(cr, SUPERUSER_ID, {})['dms.revision'].browse(rid).exists()
                        if rev and rev.storage_path:
                            rev._index_text()
                except Exception:  # noqa: BLE001
                    _logger.info("DMS: text index failed for revision %s", rid, exc_info=True)

        self.env.cr.postcommit.add(lambda: threading.Thread(target=run, name='dms-index', daemon=True).start())

    def action_submit(self):
        for r in self:
            dtype = r.document_id.type_id
            if not dtype.ai_enabled:
                continue
            if r.ai_state == 'pending':
                # it goes for approval by itself once the check passes
                r.sudo().ai_submit_after = True
                return False
            if r.ai_blocked:
                failed = [x for x in json.loads(r.ai_results or '[]') if not x['passed'] and x['blocking']]
                lines = '\n'.join('• %s: %s' % (x['rule'], x['reason']) for x in failed) or r.ai_error or ''
                raise UserError(_("This file did not pass the AI check. Upload a corrected file.\n\n%s", lines))
        return super().action_submit()

    def action_ai_recheck(self):
        for r in self:
            if r.state not in ('draft', 'in_review', 'current'):
                raise UserError(_("Only an active revision can be checked again."))
        self.sudo()._schedule_ai()
