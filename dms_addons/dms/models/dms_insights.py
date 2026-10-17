"""What the DMS learns from a file after it is stored.

For every new revision, in the background:
* sensitive data: Aadhaar, PAN, bank accounts, IFSC and card numbers (pattern + checksum, no AI);
* near-duplicates: other documents whose text is mostly the same (word 5-gram overlap, no AI);
* one AI call for the approver: a short summary, risk points, important dates (expiry, renewal,
  due) and, when there is an earlier revision, what changed since then.
The earliest coming date becomes the document's expiry date; a daily job reminds the owner.
"""
import difflib
import hashlib
import json
import logging
import re
import threading
import time
from datetime import timedelta

import psycopg2

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry

from .dms_ai import _ai_params, ai_json

_logger = logging.getLogger(__name__)

DATE_KINDS = ('expiry', 'renewal', 'due', 'valid_until', 'review')
DUP_THRESHOLD = 0.45       # share of word 5-grams in common to call two files near-duplicates (unrelated files score ~0)
DUP_CANDIDATES = 500

# ---------------------------------------------------------------------- sensitive data
_VERHOEFF_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
               [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
               [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
               [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
               [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
               [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def _verhoeff_ok(num):
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def _luhn_ok(num):
    total = 0
    for i, ch in enumerate(reversed(num)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


SENSITIVE_LABELS = {
    'aadhaar': "Aadhaar number", 'pan': "PAN", 'bank_account': "Bank account number",
    'ifsc': "IFSC code", 'card': "Card number",
}
_RE_AADHAAR = re.compile(r'(?<![\d/-])([2-9]\d{3})([ -]?)(\d{4})\2(\d{4})(?![\d/-])')
_RE_AADHAAR_WORD = re.compile(r'aadh?aar|\buid\b|uidai|ஆதார்|आधार', re.I)
_RE_PAN = re.compile(r'\b([A-Z]{3}[ABCFGHLJPTK][A-Z]\d{4}[A-Z])\b')
_RE_BANK = re.compile(r'(?:a/?c|acc(?:oun)?t|account)\s*(?:no\.?|number|num|#)?\s*[:.\-]?\s*(\d[\d -]{7,22}\d)', re.I)
_RE_IFSC = re.compile(r'\b([A-Z]{4}0[A-Z0-9]{6})\b')
_RE_CARD = re.compile(r'(?<!\d)((?:\d[ -]?){12,18}\d)(?!\d)')


def _mask(value):
    v = re.sub(r'\W', '', value)
    return '•' * max(len(v) - 4, 0) + v[-4:]


def find_sensitive(text):
    """[{kind, label, count, samples}] of personal and bank data found in the text."""
    found = {}

    def add(kind, value):
        item = found.setdefault(kind, {'kind': kind, 'label': SENSITIVE_LABELS[kind], 'values': set()})
        item['values'].add(re.sub(r'\W', '', value))

    text = text or ''
    banks = set()
    for m in _RE_BANK.finditer(text):
        digits = re.sub(r'\D', '', m.group(1))
        if 9 <= len(digits) <= 18:
            banks.add(digits)
            add('bank_account', digits)
    for m in _RE_AADHAAR.finditer(text):
        digits = m.group(1) + m.group(3) + m.group(4)
        # written as 1234 5678 9012, or a plain 12-digit number with the word Aadhaar close before it
        spaced = bool(m.group(2))
        named = bool(_RE_AADHAAR_WORD.search(text[max(0, m.start() - 60):m.start()]))
        if digits not in banks and (spaced or named) and _verhoeff_ok(digits):
            add('aadhaar', digits)
    for m in _RE_PAN.finditer(text):
        add('pan', m.group(1))
    for m in _RE_IFSC.finditer(text):
        add('ifsc', m.group(1))
    for m in _RE_CARD.finditer(text):
        digits = re.sub(r'\D', '', m.group(1))
        if 13 <= len(digits) <= 19 and digits not in banks and _luhn_ok(digits) and len(set(digits)) > 2:
            add('card', digits)
    order = ['aadhaar', 'pan', 'card', 'bank_account', 'ifsc']
    return [{'kind': k, 'label': found[k]['label'], 'count': len(found[k]['values']),
             'samples': sorted(_mask(v) for v in found[k]['values'])[:3]}
            for k in order if k in found]


# ---------------------------------------------------------------------- near-duplicates
def shingles(text, n=5):
    words = re.findall(r'[a-z0-9]+', (text or '').lower())
    if len(words) < n:
        return {' '.join(words)} if words else set()
    return {hashlib.blake2b(' '.join(words[i:i + n]).encode(), digest_size=8).digest()
            for i in range(len(words) - n + 1)}


def similarity(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def near_duplicates(env, text, exclude_doc_id=None, limit=5):
    """Stored revisions whose text is mostly the same as this text, best first."""
    mine = shingles(text)
    if len(mine) < 8:
        return []
    Rev = env['dms.revision'].sudo()
    domain = [('plain_text', '!=', False), ('state', 'not in', ('dropped', 'disposed')),
              ('document_id.state', '!=', 'discarded')]
    if exclude_doc_id:
        domain.append(('document_id', '!=', exclude_doc_id))
    best = {}
    for r in Rev.search(domain, order='id desc', limit=DUP_CANDIDATES):
        score = similarity(mine, shingles(r.plain_text))
        if score >= DUP_THRESHOLD and score > best.get(r.document_id.id, (0,))[0]:
            best[r.document_id.id] = (score, r)
    out = sorted(best.values(), key=lambda x: -x[0])[:limit]
    return [{'doc_id': r.document_id.id, 'code': r.document_id.code or '', 'name': r.document_id.name,
             'rev': r.rev_label, 'folder': r.document_id.folder_id.complete_name,
             'score': round(score, 2), 'same': score >= 0.95} for score, r in out]


# ---------------------------------------------------------------------- AI review
REVIEW_PROMPT = """You help approvers review company documents before they approve them. Answer only with JSON:
{"summary": "<2 or 3 short sentences: what the document is, who it is between, amounts, period>",
 "risks": [{"level": "high|medium|low", "point": "<short, specific: what to check and why>"}],
 "dates": [{"kind": "expiry|renewal|due|valid_until|review|issue|other", "label": "<what the date is>", "date": "YYYY-MM-DD"}],
 "changes": {"summary": "<one sentence>", "items": ["<each meaningful change: old value -> new value>"]}}
Risks to look for: missing signature or stamp, blank fields, amounts or totals that do not agree, unusual payment
or penalty terms, auto-renewal, a date already passed, a party or GST number that looks wrong. Give an empty list
when there is nothing real; never invent problems. Dates: only dates printed in the document, converted to
YYYY-MM-DD. "due" and "expiry" are only for something still to happen (a payment still owed, a licence or contract
ending); a date when something was already done (paid, signed, issued, received) is "issue" or "other". "changes" is null when no earlier version is given; ignore spacing and layout changes."""


def _diff_lines(old, new, limit=3500):
    norm = lambda t: [re.sub(r'\s+', ' ', l).strip() for l in (t or '').splitlines() if l.strip()]
    out = []
    for line in difflib.unified_diff(norm(old), norm(new), lineterm='', n=0):
        if line.startswith(('---', '+++', '@@')):
            continue
        out.append(line)
    text = '\n'.join(out)
    return text[:limit] + ('\n…' if len(text) > limit else '')


class DmsRevisionInsights(models.Model):
    _inherit = 'dms.revision'

    plain_text = fields.Text(readonly=True, copy=False, help="The file's text, kept for comparing revisions and duplicates.")
    insight_state = fields.Selection([('pending', 'Reading'), ('done', 'Done'), ('error', 'Failed'), ('none', 'No text')],
                                     readonly=True, copy=False)
    insight_error = fields.Char(readonly=True, copy=False)
    ai_review = fields.Text(readonly=True, copy=False)        # JSON {summary, risks}
    ai_dates = fields.Text(readonly=True, copy=False)         # JSON [{kind, label, date}]
    ai_changes = fields.Text(readonly=True, copy=False)       # JSON {summary, items, against}
    sensitive_json = fields.Text(readonly=True, copy=False)   # JSON [{kind, label, count, samples}]
    duplicates_json = fields.Text(readonly=True, copy=False)  # JSON [{doc_id, code, name, score, same}]
    has_sensitive = fields.Boolean(readonly=True, copy=False, index=True)

    def _previous_revision(self):
        self.ensure_one()
        earlier = self.document_id.revision_ids.filtered(
            lambda r: r.id < self.id and r.storage_path and r.state not in ('dropped', 'disposed'))
        good = earlier.filtered(lambda r: r.state in ('current', 'superseded', 'archived'))
        return (good or earlier).sorted('id')[-1:] if (good or earlier) else self.browse()

    def _text_for_insights(self):
        self.ensure_one()
        if not self.plain_text:
            text = self._ai_text()
            if text:
                self.plain_text = text
        return self.plain_text or ''

    def _build_insights(self):
        self.ensure_one()
        text = self._text_for_insights()
        if not text:
            self.write({'insight_state': 'none', 'insight_error': False})  # photos, audio, video: nothing to read
            return
        sensitive = find_sensitive(text)
        dups = near_duplicates(self.env, text, exclude_doc_id=self.document_id.id)
        vals = {'sensitive_json': json.dumps(sensitive), 'has_sensitive': bool(sensitive),
                'duplicates_json': json.dumps(dups), 'insight_state': 'done', 'insight_error': False}
        if _ai_params(self.env)['enabled']:
            prev = self._previous_revision()
            diff = _diff_lines(prev._text_for_insights(), text) if prev else ''
            today = fields.Date.context_today(self)
            user = "TODAY: %s\nTYPE: %s\nTITLE: %s\n\nDOCUMENT TEXT:\n%s" % (
                today, self.document_id.type_id.name, self.document_id.name, text[:5500])
            if prev:
                user += "\n\nCHANGES FROM REVISION %s (- removed, + added):\n%s" % (
                    prev.rev_label, diff or "(no text changes)")
            try:
                ans = ai_json(self.env, REVIEW_PROMPT, user, max_tokens=1000)
                risks = [r for r in (ans.get('risks') or []) if isinstance(r, dict) and r.get('point')]
                for r in risks:
                    r['level'] = r.get('level') if r.get('level') in ('high', 'medium', 'low') else 'medium'
                dates = []
                for d in ans.get('dates') or []:
                    try:
                        dates.append({'kind': str(d.get('kind') or 'other'), 'label': str(d.get('label') or '')[:80],
                                      'date': fields.Date.to_string(fields.Date.to_date(str(d.get('date'))[:10]))})
                    except (ValueError, TypeError, AttributeError):
                        continue
                changes = ans.get('changes') if prev and isinstance(ans.get('changes'), dict) else None
                if changes is not None:
                    changes = {'summary': str(changes.get('summary') or ''), 'against': prev.rev_label,
                               'items': [str(x) for x in (changes.get('items') or [])][:12]}
                    if not diff:
                        changes = {'summary': _("No change in the text since revision %s.", prev.rev_label),
                                   'against': prev.rev_label, 'items': []}
                vals.update({'ai_review': json.dumps({'summary': str(ans.get('summary') or ''), 'risks': risks[:8]}),
                             'ai_dates': json.dumps(dates), 'ai_changes': json.dumps(changes) if changes else False})
            except UserError as e:
                vals.update({'insight_state': 'error', 'insight_error': str(e)[:250]})
        self.write(vals)
        self.document_id._update_expiry()
        if sensitive:
            self.env['dms.log'].sudo().log('ai_check', revision=self, details=_(
                "Sensitive data found: %s", ', '.join('%s ×%s' % (s['label'], s['count']) for s in sensitive)))

    def _schedule_insights(self):
        if not self:
            return
        self.write({'insight_state': 'pending', 'insight_error': False})
        dbname, ids = self.env.cr.dbname, self.ids

        def run():
            for rid in ids:
                for attempt in range(3):  # the AI check thread may be writing the same document
                    try:
                        with Registry(dbname).cursor() as cr:
                            rev = api.Environment(cr, SUPERUSER_ID, {})['dms.revision'].browse(rid).exists()
                            if rev and rev.storage_path:
                                rev._build_insights()
                        break
                    except psycopg2.errors.SerializationFailure:
                        time.sleep(3 + attempt * 3)
                    except Exception as e:  # noqa: BLE001
                        _logger.exception("DMS insights failed for revision %s", rid)
                        try:
                            with Registry(dbname).cursor() as cr:
                                api.Environment(cr, SUPERUSER_ID, {})['dms.revision'].browse(rid).write(
                                    {'insight_state': 'error', 'insight_error': str(e)[:250]})
                        except Exception:  # noqa: BLE001
                            pass
                        break

        self.env.cr.postcommit.add(lambda: threading.Thread(target=run, name='dms-insights', daemon=True).start())

    @api.model
    def _create_with_file(self, doc, filename, data, note):
        rev = super()._create_with_file(doc, filename, data, note)
        rev._schedule_insights()
        return rev

    def action_rebuild_insights(self):
        self.sudo()._schedule_insights()
        return True

    @api.model
    def _rebuild_all_insights(self):
        """Once, for files stored before insights existed."""
        todo = self.sudo().search([('insight_state', '=', False), ('storage_path', '!=', False),
                                   ('state', 'not in', ('dropped', 'disposed'))])
        for r in todo:
            try:
                r._build_insights()
                self.env.cr.commit()
            except Exception:  # noqa: BLE001
                _logger.exception("DMS insights failed for revision %s", r.id)
                self.env.cr.rollback()
        return len(todo)


class DmsDocumentInsights(models.Model):
    _inherit = 'dms.document'

    expiry_date = fields.Date("Expires / due", readonly=True, index=True,
                              help="Earliest coming expiry, renewal or due date the AI found in the current file.")
    expiry_label = fields.Char(readonly=True)
    expiry_notified = fields.Date(readonly=True, help="The owner was reminded for this date.")

    def _insight_revision(self):
        self.ensure_one()
        return self.current_revision_id or self.revision_ids.filtered(
            lambda r: r.state not in ('dropped', 'disposed', 'rejected')).sorted('id')[-1:]

    def _update_expiry(self):
        today = fields.Date.context_today(self)
        for d in self:
            rev = d._insight_revision()
            dates = [x for x in json.loads(rev.ai_dates or '[]') if x.get('kind') in DATE_KINDS
                     and not re.search(r'\b(paid|issued|signed|received|created|dated)\b', x.get('label') or '', re.I)] if rev else []
            coming = sorted((x for x in dates if fields.Date.to_date(x['date']) >= today), key=lambda x: x['date'])
            past = sorted((x for x in dates if fields.Date.to_date(x['date']) < today), key=lambda x: x['date'])
            pick = coming[0] if coming else (past[-1] if past else None)
            new = fields.Date.to_date(pick['date']) if pick else False
            vals = {'expiry_date': new, 'expiry_label': pick['label'] if pick else False}
            if new != d.expiry_date:
                vals['expiry_notified'] = False
            if new != d.expiry_date or vals['expiry_label'] != (d.expiry_label or False):
                d.sudo().write(vals)  # only on a real change: "last edited" stays the user's

    @api.model
    def _cron_expiry_reminders(self):
        days = int(self.env['ir.config_parameter'].sudo().get_param('dms.expiry_alert_days') or 30)
        today = fields.Date.context_today(self)
        due = self.sudo().search([('expiry_date', '!=', False), ('expiry_date', '<=', today + timedelta(days=days)),
                                  ('expiry_date', '>=', today - timedelta(days=1)), ('expiry_notified', '=', False),
                                  ('state', 'in', ('published', 'draft', 'in_review'))])
        for d in due:
            left = (d.expiry_date - today).days
            d.with_context(mail_activity_quick_update=True).activity_schedule('mail.mail_activity_data_todo', date_deadline=d.expiry_date, user_id=d.owner_id.id,
                                summary=_("DMS: %(doc)s - %(what)s on %(date)s", doc=d.display_name,
                                          what=d.expiry_label or _("expires"), date=d.expiry_date))
            d.message_post(body=_("%(what)s on %(date)s (%(n)s days left). Renew or replace the document in time.",
                                  what=d.expiry_label or _("Expires"), date=d.expiry_date, n=left),
                           subtype_xmlid='mail.mt_note')
            d._expiry_mail(left)
            d.expiry_notified = today
        return len(due)

