"""Ask DMS: an assistant that answers questions about the documents and about the DMS itself.

One question goes through four steps:
  1. understand  the AI turns the message (and a pasted screenshot's text) into a plan
  2. find        meaning search (vector index) + database, every document checked against the
                 same access rules as the rest of the DMS
  3. act         count, status, password, access request, help ... in Python, never by the AI
  4. answer      the AI writes a short, human reply from the facts it is allowed to see

What the AI may read: titles of documents the user can list, and content (text, amounts,
summaries) only of documents the user can open. A document the user cannot open shows its
title and a Request access button; drafts of other people never appear at all.
"""
import base64
import json
import logging
import os
import re
import shutil
import tempfile
from collections import Counter, OrderedDict
from datetime import datetime, timedelta

from odoo import fields, http, _
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..models.dms_ai import _num, _ocr_image, ai_json
from ..models.dms_folder import ACCESS_LEVELS
from ..models.dms_insights import shingles, similarity
from ..models.dms_vector import vector_search
from .main import DmsController, _env

_logger = logging.getLogger(__name__)

MEANING_MIN = 0.55      # bge-small cosine: below this a chunk is only loosely related
LEVEL_RANK = {False: 0, None: 0, 'none': 0, 'view': 1, 'download': 2, 'edit': 3}

HELP = """DMS GUIDE (answer how-to questions only from this)
- Home: your folders. Dashboard: your numbers, activity, unusual activity (Document Controllers), things needing attention.
- Folders: each department has a folder. Access levels: View (read inside Odoo), View & Download, Edit (upload, check out, share).
  Access given on a folder applies to every file inside; a single file can be excluded or given a different level.
- Upload (button top left, or drop a file on a folder): needs Edit on the folder. The AI reads the file, picks the type,
  folder, title, description, then checks the type's rules. Upload is possible only when every required rule passes.
- Document numbers look like ACC-INV-001 (folder code - type code - number). Numbers are never reused.
- Approval: Send for approval -> L1 reviewer -> L2 approver (-> L3 final authority for some types). Each approver gets
  an email and a bell notification with Approve / Refuse. When the last one approves, the revision is Published (Rev 0, 1...).
  A refusal sends it back to the author with the reason.
- Revisions: Check out (Edit access, published document) -> download, edit -> Check in the new version -> it goes for
  approval -> when published, the old revision stays visible until its cut-off date, then moves to the Archive.
- Buttons and who sees them: Open = View access. Download = View & Download access. Check out / Move / Share = Edit access.
  Retire and the trash (delete permanently) = Document Controller only. Approve / Refuse = only the approver whose turn it is
  (being Document Controller does NOT let you approve unless you are that step's approver).
- Check out = you take the published document to make a new version: press Check out to edit, download it, edit it on your
  computer, then press Check in new version and choose the edited file; it goes for approval. While checked out, nobody else can
  check it out and readers still see the approved version. Cancel check-out drops your pending changes.
  Bring cut-off forward = an approver of the folder or the Controller. Access requests and Delete requests pages = Document Controller.
- No access to a document: in All documents or here, press Request access, choose the level and give a reason. Controllers
  and people with Edit on the folder decide; you get an email and a notification.
- Opening a file inside the DMS never asks a password. A downloaded file may be password protected (set by the Controller in
  Settings); the assistant tells that password to people with download access. A file that already had its own password when it
  was uploaded keeps it: ask the person who uploaded it.
- Retire sends a document to the Archive (read only). After its retention period it can be destroyed through a Disposal request
  approved by the Controller and the Department Head.
- Search: the search box (words, or plain questions with AI), voice in Tamil or English, Ctrl+K command palette.
- Notifications: the bell in the top bar and the floating bell; approvals can be done from there.
"""

PLAN_PROMPT = """You are the planner of a document management assistant. Read the user's message (English, Tamil or
Tanglish) and the conversation so far, and answer only with JSON:
{"intent": "find|count|status|document|identify|access_list|data|password|request_access|my_work|help|greeting|other",
 "search": "<English phrase describing the documents or content wanted, for a meaning search; '' if none>",
 "type": "<one document type from the list, or ''>",
 "party": "<company, vendor, customer or person names mentioned, comma separated, or ''>",
 "doc_ref": "<document number or title the user points to; use the conversation for 'this', 'it', 'that one'; or ''>",
 "date_from": "YYYY-MM-DD or ''", "date_to": "YYYY-MM-DD or ''",
 "state": "draft|in_review|published|archived|''",
 "level": "view|download|edit|''", "reason": "<reason for an access request if the user gave one, else ''>",
 "question": "<the content question to answer from the documents, in English, or ''>",
 "access_level": "view|download|edit|any|''  (for access_list: which level the user asks about)",
 "data": {"dataset": "documents|approvals|access_requests|folders|audit_log", "filters": [["field", "=|!=|contains|>|<|>=|<=", "value"]],
          "group_by": "<field or ''>", "sum": "<numeric field or ''>", "order": "<field or ''>", "desc": true, "limit": 50}}
Intents: find = show documents or answer a question from their content; count = how many / total / sum;
status = where is a document, its stage, who has it; document = tell me about / details of one document;
identify = the user pasted a screenshot and asks what it is; access_list = who has access to a document (or only view access);
data = a question answered by listing, filtering, counting or grouping records (approvals pending longer than 3 days,
documents per folder, who uploaded most, refused approvals, access requests of a person, my folders' approvers ...).
"how many per/each/by X" or a list with conditions is data (with group_by for per/each/by); count is only for
"how many documents about something" (a vendor, a type, a topic).
Examples (intent data):
- "documents per folder" -> {"dataset": "documents", "group_by": "folder"}
- "how many documents are published" -> {"dataset": "documents", "filters": [["state", "=", "published"]]}
- "show refused documents" -> {"dataset": "documents", "filters": [["state", "=", "rejected"]]}
- "my drafts" -> {"dataset": "documents", "filters": [["owner", "=", "me"], ["state", "=", "draft"]]}
- "which document expires next" -> {"dataset": "documents", "filters": [["expires", "!=", ""]], "order": "expires", "limit": 5}
- "documents with PAN / Aadhaar / personal data" -> {"dataset": "documents", "filters": [["personal_data", "!=", ""]]}
- "which documents are checked out" -> {"dataset": "documents", "filters": [["checked_out_by", "!=", ""]]}
- "documents uploaded this week" -> {"dataset": "documents", "filters": [["created", ">=", "<monday YYYY-MM-DD>"]]}
- "waiting approvals" -> {"dataset": "approvals", "filters": [["status", "=", "waiting"]], "order": "waiting_days", "desc": true}
- "who uploaded most" -> {"dataset": "documents", "group_by": "owner"}
- "audit log of today" -> {"dataset": "audit_log", "filters": [["date", "=", "<today>"]]}
- "who downloaded files today" -> {"dataset": "audit_log", "filters": [["action", "=", "download"], ["date", "=", "<today>"]], "group_by": "user"}
- "pending access requests" -> {"dataset": "access_requests", "filters": [["status", "=", "pending"]]}
Use intent count (not data) only for a topic, vendor or document type count ("how many Axles India invoices").
DATASETS (data intent; use only these fields):
%(datasets)s
password = a password for a file; request_access = the user wants access; my_work = what is waiting for me, my documents,
my approvals; help = how the DMS works, which button, who can do what; greeting = hello/thanks.
The "data" key is only needed for intent data.
Dates: only when the user says a time ("last month", "October", "this year"); today is %(today)s."""

ANSWER_PROMPT = """You are "Ask DMS", a friendly, sharp assistant inside a company document management system,
answering like a senior colleague who knows the system inside out.
Write the reply from the FACTS (and the GUIDE when given) only. Never invent documents, numbers, names or passwords.
FORMAT: Markdown. Short answers for simple questions (1 to 3 sentences). For detailed questions (a document's details,
how something works, who has access, lists) use structure: a one-line summary, then ### headings, bullet lists,
numbered steps, and markdown tables (| a | b |) where they help. Bold key numbers and document numbers.
- Reply in the user's language style: %(language)s (tanglish = Tamil words in English letters, mixed with English).
- Talk TO the user ("you" / "neenga", "ungalukku"); never speak as if you were the user ("enakku", "I need").
- The cards below your reply already show the documents, tables and buttons. Never write a Markdown table or a list
  of more than 3 documents when a card shows them: give the headline number and one useful observation instead.
- If some documents need access, say how many and that they can press Request access.
- If nothing was found, say so plainly and suggest a better question.
- How-to and "who can" questions: answer from the GUIDE only, with numbered steps or a table. Never claim the user has a right the guide does not give.
- Never write the download password; never mention the server password.
Answer only with JSON: {"answer": "<reply>", "suggestions": ["<2 to 4 short follow-up questions the user may ask next, same language>"]}"""


KB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'dms_kb.md')
_KB = None

TANGLISH = re.compile(r'\b(enna|ennaku|enakku|ungalukku|iruku|irukku|irukka|irukkaa|panna|pannu|pannunga|sollu|sollunga|evlo|ethana|'
                      r'yaar|yaaru|yarukula|yaarukku|eppadi|epdi|epo|eppo|venum|vendam|illa|illai|ah|la|ku|kitta|pathi|nu|da|pa|'
                      r'kudu|kudunga|paaru|paarunga|theriyum|theriyala|aagum|aachu|mudiyuma|mudiyum|intha|indha|antha|andha|enga)\b', re.I)


def detect_language(text):
    """english / tamil / tanglish, from the user's own words (not from the AI)."""
    if not text:
        return 'english'
    if re.search(r'[\u0B80-\u0BFF]', text):
        return 'tamil'
    words = re.findall(r'[a-zA-Z]+', text)
    hits = len(TANGLISH.findall(text))
    return 'tanglish' if words and hits >= max(1, len(words) // 6) else 'english'


def kb_sections():
    global _KB
    if _KB is None:
        try:
            with open(KB_PATH, encoding='utf-8') as f:
                raw = f.read()
        except OSError:
            raw = ''
        _KB = [('## ' + part).strip() for part in raw.split('\n## ') if part.strip()]
        if _KB and _KB[0].startswith('## ## '):
            _KB[0] = _KB[0][3:]
    return _KB


def kb_for(question, limit=6500):
    """The guide sections that share the most words with the question (plus the roles basics)."""
    words = {w for w in re.findall(r'[a-z]{3,}', (question or '').lower())}
    synonyms = {'button': 'buttons', 'permission': 'access', 'rights': 'access', 'approve': 'approval', 'approver': 'approval',
                'password': 'passwords', 'delete': 'disposal', 'remove': 'disposal', 'version': 'revisions', 'model': 'data',
                'table': 'data', 'field': 'data', 'workflow': 'approval', 'flow': 'approval', 'mail': 'emails', 'email': 'emails',
                'notification': 'notifications', 'bell': 'notifications', 'checkout': 'revisions', 'check': 'revisions'}
    words |= {synonyms[w] for w in words if w in synonyms}
    scored = []
    for sec in kb_sections():
        low = sec.lower()
        title = low.split('\n', 1)[0]
        score = sum(low.count(w) for w in words) + 6 * sum(1 for w in words if w in title)
        scored.append((score, sec))
    scored.sort(key=lambda x: -x[0])
    out, size = [], 0
    for score, sec in scored:
        if score <= 0 and out:
            break
        if size + len(sec) > limit:
            continue
        out.append(sec)
        size += len(sec)
    return '\n\n'.join(out) or '\n\n'.join(kb_sections()[:3])


# read-only data the AI may ask for; rows are built by Odoo and filtered by access first
DATASETS = {
    'documents': "code, title, state (draft|in_review|published|rejected|archived), type, folder, owner, revision, created, "
                 "published_on, expires, checked_out_by, personal_data (Aadhaar/PAN/bank numbers found), vendor, buyer, amount",
    'approvals': "document, revision, level (L1|L2|L3), approver, status (waiting|approved|refused|skipped), decided_on, "
                 "waiting_days, comment",
    'access_requests': "document, requested_by, level, status (pending|approved|refused), requested_on, decided_by",
    'folders': "folder, department, parent, l1_reviewer, l2_approver, l3_final, my_access, documents",
    'audit_log': "date, user, action (view|download|upload|approve|refuse|publish|checkout|checkin|access_grant|...), "
                 "document, details   (Document Controllers only)",
}


def _clip(s, n):
    s = re.sub(r'\s+', ' ', s or '').strip()
    return s if len(s) <= n else s[:n - 1] + '…'


class DmsAssistant(http.Controller):

    # ------------------------------------------------------------------ access
    def _vis(self, ctrl, d, user, controller):
        """'open' (may read it), 'listed' (may see the title and ask for access) or 'hidden'."""
        if d._blocked_for(user):
            return 'hidden'
        if ctrl._can_see_doc(d, user):
            if d.state in ('draft', 'rejected', 'in_review') and not d.current_revision_id and not controller \
                    and d.owner_id != user and user not in d.revision_ids.approval_ids.user_id:
                return 'hidden'
            return 'open'
        return 'listed' if d.state in ('published', 'archived') else 'hidden'

    # ------------------------------------------------------------------ facts of one document
    def _fields(self, d):
        rev = d._insight_revision()
        try:
            return json.loads(rev.ai_fields_json or '{}') if rev else {}
        except ValueError:
            return {}

    def _amount(self, found):
        for k in ('total_amount', 'grand_total', 'amount', 'total', 'invoice_total', 'net_amount'):
            v = _num(found.get(k)) if found.get(k) not in (None, '') else None
            if v:
                return v
        return None

    def _date(self, d, found):
        for k in ('invoice_date', 'date', 'document_date', 'bill_date'):
            v = found.get(k)
            if not v:
                continue
            for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%d %b %Y', '%d.%m.%Y'):
                try:
                    return datetime.strptime(str(v).strip()[:11], fmt).date()
                except ValueError:
                    continue
        return (d.current_revision_id.approved_date or d.create_date.date()) if d.create_date else None

    def _card(self, ctrl, d, user, vis, score=None, snippet=None):
        card = ctrl._doc_card(d, user)
        card['vis'] = vis
        card['score'] = score
        if vis == 'open':
            found = self._fields(d)
            card['facts'] = [[k.replace('_', ' ').title(), str(v)] for k, v in found.items() if v not in (None, '')][:4]
            card['snippet'] = _clip(snippet, 220) if snippet else ''
        else:
            card['facts'] = []
            card['snippet'] = ''
        return card

    # ------------------------------------------------------------------ finding documents
    def _find_ref(self, env, ref, context_ids):
        """The document the user points to: number, title, or the one we just talked about."""
        Doc = env['dms.document'].sudo()
        live = [('state', '!=', 'discarded')]
        ref = (ref or '').strip()
        if ref:
            m = re.search(r'[A-Z]{2,5}-[A-Z]{2,5}-\d{3,}', ref.upper())
            if m:
                d = Doc.search(live + [('code', '=', m.group(0))], limit=1)
                if d:
                    return d
            d = Doc.search(live + ['|', ('name', '=ilike', ref), ('code', '=ilike', ref)], limit=1) \
                or Doc.search(live + [('name', 'ilike', ref)], limit=1)
            if d:
                return d
            words = [w for w in re.findall(r'\w+', ref) if len(w) > 2]
            if words:
                dom = live[:]
                for w in words[:4]:
                    dom.append(('name', 'ilike', w))
                d = Doc.search(dom, limit=1)
                if d:
                    return d
            hits = vector_search(env, ref, k=3)
            if hits and hits[0]['score'] >= MEANING_MIN:
                return Doc.browse(hits[0]['doc_id']).exists()
        if context_ids:
            return Doc.browse(context_ids[0]).exists()
        return Doc.browse()

    def _candidates(self, env, plan):
        """Documents that match the plan, best first, with their best matching text."""
        Doc = env['dms.document'].sudo()
        domain = [('state', '!=', 'discarded')]
        if plan.get('type'):
            domain.append(('type_id.name', 'ilike', plan['type']))
        if plan.get('state') in ('draft', 'in_review', 'published', 'archived'):
            domain.append(('state', '=', plan['state']))
        parties = [p.strip() for p in (plan.get('party') or '').split(',') if len(p.strip()) > 1]
        for p in parties:
            # every word of the name must be there: "Axles India" finds "AXLES INDIA PVT LTD"
            sub = []
            for w in [w for w in re.findall(r'\w+', p) if len(w) > 1][:4]:
                sub.append(['|', '|', ('ai_index', 'ilike', w), ('name', 'ilike', w), ('description', 'ilike', w)])
            for s in sub:
                domain += s
        scores, snippets = OrderedDict(), {}
        query = plan.get('question') or plan.get('search') or ''
        if query:
            for h in vector_search(env, query, k=40):
                if h['score'] < MEANING_MIN and (parties or plan.get('type')):
                    continue
                if h['doc_id'] not in scores:
                    scores[h['doc_id']] = h['score']
                    snippets[h['doc_id']] = h['text'].split('\n', 1)[-1]
        filtered = bool(parties or plan.get('type') or plan.get('state'))
        if filtered:
            docs = Doc.search(domain, limit=300)
            ranked = sorted(docs, key=lambda d: -(scores.get(d.id) or 0))
        else:
            good = [i for i, s in scores.items() if s >= MEANING_MIN]
            ranked = list(Doc.browse(good).exists())
            if not ranked and query:
                words = [w for w in re.findall(r'\w+', query) if len(w) > 3][:4]
                if words:
                    dom = domain[:]
                    for w in words:
                        dom += ['|', '|', ('ai_index', 'ilike', w), ('name', 'ilike', w), ('code', 'ilike', w)]
                    ranked = list(Doc.search(dom, limit=50))
        out = []
        for d in ranked:
            if plan.get('date_from') or plan.get('date_to'):
                dt = self._date(d, self._fields(d))
                try:
                    if plan.get('date_from') and dt and dt < fields.Date.to_date(plan['date_from']):
                        continue
                    if plan.get('date_to') and dt and dt > fields.Date.to_date(plan['date_to']):
                        continue
                except ValueError:
                    pass
            out.append((d, scores.get(d.id), snippets.get(d.id)))
        return out

    # ------------------------------------------------------------------ the screenshot
    def _read_image(self, data_url):
        m = re.match(r'data:image/(\w+);base64,(.*)', data_url or '', re.S)
        if not m:
            raise UserError(_("Paste or attach a picture (PNG or JPG)."))
        raw = base64.b64decode(m.group(2))
        if len(raw) > 8 * 1024 * 1024:
            raise UserError(_("The picture is too big (8 MB at most)."))
        tmp = tempfile.mkdtemp(prefix='dms_ask_')
        try:
            path = os.path.join(tmp, 'shot.' + ('jpg' if m.group(1) in ('jpeg', 'jpg') else 'png'))
            with open(path, 'wb') as f:
                f.write(raw)
            return (_ocr_image(path) or ''), raw
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _dhash(raw, size=8):
        """Perceptual hash: two pictures of the same thing differ by a few bits, unrelated ones by ~32."""
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw)).convert('L').resize((size + 1, size))
        px = list(img.getdata())
        bits = 0
        for row in range(size):
            for col in range(size):
                bits = (bits << 1) | (px[row * (size + 1) + col] > px[row * (size + 1) + col + 1])
        return bits

    def _identify(self, env, text, raw=None):
        """Which stored document is this picture of?  [(doc, confidence, how)], best first, only confident ones.
        - a photo / image file: compare the pictures themselves (perceptual hash)
        - a screenshot of a page: enough of its words must appear in one stored file
        - meaning search only as a last resort, with a high bar"""
        Rev = env['dms.revision'].sudo()
        live = [('storage_path', '!=', False), ('state', 'not in', ('dropped', 'disposed')), ('document_id.state', '!=', 'discarded')]
        found = {}

        def keep(d, score, how):
            if score > found.get(d.id, (0,))[0]:
                found[d.id] = (score, d, how)

        # 1. the picture itself
        if raw:
            try:
                mine = self._dhash(raw)
                for r in Rev.search(live, limit=400):
                    if r._preview_kind() not in ('image', 'image_convert', 'pdf', 'doc'):
                        continue
                    try:
                        thumb = r._thumb_png()
                    except Exception:  # noqa: BLE001 - no picture of that file
                        continue
                    if not thumb:
                        continue
                    dist = bin(mine ^ self._dhash(thumb)).count('1')
                    if dist <= 10:
                        keep(r.document_id, round(1 - dist / 64.0, 2), 'picture')
            except Exception:  # noqa: BLE001
                _logger.info("Ask DMS: picture comparison skipped", exc_info=True)
        # 2. the words on it: a real page has many words, and most of them must be in the same file
        words = re.findall(r'[A-Za-z0-9]{3,}', text or '')
        mine = shingles(text, 3)
        if len(words) >= 15 and len(mine) >= 12:
            for r in Rev.search(live + [('plain_text', '!=', False)], limit=1500):
                theirs = shingles(r.plain_text, 3)
                if theirs:
                    overlap = len(mine & theirs) / len(mine)
                    if overlap >= 0.45:
                        keep(r.document_id, round(min(overlap, 1.0), 2), 'text')
        # 3. meaning, only for a text-rich picture and a very close match
        if not found and len(words) >= 25:
            for h in vector_search(env, text[:1500], k=3):
                if h['score'] >= 0.75:
                    keep(env['dms.document'].sudo().browse(h['doc_id']), round(h['score'], 2), 'meaning')
        out = sorted(found.values(), key=lambda x: -x[0])
        return [(d, score, how) for score, d, how in out][:3]

    # ------------------------------------------------------------------ cards
    def _status_card(self, ctrl, d, user, vis):
        now = fields.Datetime.now()
        pending = d.revision_ids.filtered(lambda r: r.state in ('draft', 'in_review')).sorted('id')[-1:]
        cur = d.current_revision_id
        steps = [{'label': _("Uploaded"), 'state': 'done', 'who': (d.revision_ids.sorted('id')[:1].author_id.name or ''),
                  'date': fields.Datetime.to_string(d.create_date)}]
        waiting = None
        rev = pending or cur
        if rev and rev.submitted_date:
            steps.append({'label': _("Sent for approval"), 'state': 'done', 'who': rev.author_id.name,
                          'date': fields.Datetime.to_string(rev.submitted_date)})
        if rev:
            for a in rev.approval_ids.sorted('sequence'):
                st = {'approved': 'done', 'refused': 'bad', 'waiting': 'wait', 'skipped': 'skip'}[a.state]
                if a.state == 'waiting' and not waiting and rev.state == 'in_review':
                    st = 'now'
                    waiting = {'who': a.user_id.name, 'level': a.level.upper(),
                               'days': (now - rev.submitted_date).days if rev.submitted_date else 0}
                steps.append({'label': '%s %s' % (a.level.upper(), {'done': _("approved"), 'bad': _("refused"), 'now': _("waiting"),
                                                                    'wait': _("next"), 'skip': _("not needed")}[st]),
                              'state': st, 'who': a.user_id.name, 'date': fields.Datetime.to_string(a.date) if a.date else False,
                              'comment': a.comment if vis == 'open' else ''})
        if cur:
            steps.append({'label': _("Published Rev %s", cur.rev_label), 'state': 'done', 'who': '',
                          'date': fields.Date.to_string(cur.approved_date) if cur.approved_date else False})
        stage = {'draft': _("Draft, not sent yet"), 'in_review': _("In review"), 'published': _("Published"),
                 'rejected': _("Refused, back with the author"), 'archived': _("Archived")}.get(d.state, d.state)
        if pending and pending.state == 'in_review' and d.state == 'published':
            stage = _("Published, new version in review")
        level = d._access_for_user(user) or False
        card = self._card(ctrl, d, user, vis)
        card.update({'kind': 'status', 'stage': stage, 'steps': steps, 'waiting': waiting, 'my_level': level,
                     'my_level_label': dict(ACCESS_LEVELS).get(level, _("No access")) if level else _("No access")})
        if vis == 'open' and rev:
            card['ai'] = {'state': rev.ai_state or False, 'blocked': rev.ai_blocked,
                          'passed': sum(1 for x in json.loads(rev.ai_results or '[]') if x.get('passed')),
                          'total': len(json.loads(rev.ai_results or '[]'))}
            if d.locked_by_id:
                card['locked_by'] = d.locked_by_id.name
            if d.expiry_date:
                card['expiry'] = {'date': fields.Date.to_string(d.expiry_date), 'label': d.expiry_label or ''}
        return card

    def _stats_card(self, rows):
        """rows: [(doc, vis)] -> tiles + per month bars + biggest parties, from documents the user may open."""
        open_rows = [d for d, v in rows if v == 'open']
        total_amount, months, currency = 0.0, Counter(), ''
        for d in open_rows:
            found = self._fields(d)
            amt = self._amount(found)
            if amt:
                total_amount += amt
            currency = currency or str(found.get('currency') or '')
            dt = self._date(d, found)
            if dt:
                months[dt.strftime('%Y-%m')] += 1
        bars = [{'label': datetime.strptime(k, '%Y-%m').strftime('%b %Y'), 'count': v} for k, v in sorted(months.items())][-12:]
        return {'kind': 'stats', 'total': len(rows), 'open': len(open_rows),
                'listed': sum(1 for _d, v in rows if v == 'listed'),
                'amount': round(total_amount, 2) if total_amount else 0, 'currency': currency, 'months': bars}

    # ------------------------------------------------------------------ one document, like opening its record
    def _record_card(self, ctrl, d, user, vis):
        card = self._status_card(ctrl, d, user, vis)
        card['kind'] = 'record'
        cur = d.current_revision_id or d.revision_ids.sorted('id')[-1:]
        dt = fields.Datetime.to_string
        card['grid'] = [
            [_("Number"), d.code or ''], [_("Type"), d.type_id.name or ''], [_("Folder"), d.folder_id.complete_name or ''],
            [_("Owner"), d.owner_id.name or ''], [_("Status"), card['stage']],
            [_("Current revision"), cur.rev_label if cur and cur.rev_no >= 0 else _("Not published yet")],
            [_("Created"), dt(d.create_date)[:16] if d.create_date else ''], [_("Last change"), dt(d.write_date)[:16] if d.write_date else ''],
            [_("Your access"), card['my_level_label']],
        ]
        if vis != 'open':
            return card
        card['grid'] += [[_("File"), cur.file_name or ''], [_("Size"), '%.1f KB' % ((cur.file_size or 0) / 1024.0)]]
        if d.locked_by_id:
            card['grid'].append([_("Checked out by"), d.locked_by_id.name])
        if d.expiry_date:
            card['grid'].append([d.expiry_label or _("Expires"), fields.Date.to_string(d.expiry_date)])
        if d.description:
            card['grid'].append([_("Description"), d.description])
        rev = d._insight_revision()
        review = json.loads(rev.ai_review or 'null') or {} if rev else {}
        card['summary'] = review.get('summary') or (rev.ai_summary if rev else '') or ''
        card['risks'] = [r for r in review.get('risks', []) if r.get('point')][:6]
        card['fields_table'] = [[k.replace('_', ' ').title(), str(v)] for k, v in self._fields(d).items() if v not in (None, '')]
        card['dates'] = json.loads(rev.ai_dates or '[]') if rev else []
        card['revisions'] = [{'rev': r.rev_label, 'state': dict(r._fields['state'].selection).get(r.state), 'file': r.file_name,
                              'author': r.author_id.name, 'approved': fields.Date.to_string(r.approved_date) if r.approved_date else '',
                              'note': r.change_note or ''} for r in d._visible_revisions(user).sorted('id', reverse=True)]
        if rev and rev.sensitive_json:
            card['sensitive'] = [x['label'] for x in json.loads(rev.sensitive_json or '[]')]
        return card

    # ------------------------------------------------------------------ who has access
    def _may_see_access(self, d, user):
        """Same rule as the document's Access tab."""
        return user.has_group('dms.group_dms_controller') or d.owner_id == user or d._access_for_user(user) == 'edit'

    def _access_rows(self, d):
        env = d.env
        chain = env['dms.folder'].sudo().search([('id', 'parent_of', d.folder_id.id)])
        users = env.ref('dms.group_dms_controller').sudo().user_ids
        lines = env['dms.access'].sudo().search([('folder_id', 'in', chain.ids), ('level', '!=', 'none')])
        users |= lines.user_id | lines.group_id.user_ids | d.access_ids.user_id | d.owner_id
        users |= env['dms.group'].sudo().search([('folder_ids', 'in', chain.ids)]).user_ids
        labels = dict(ACCESS_LEVELS)
        rows = []
        for u in users.filtered(lambda u: u.active and not u.share):
            level = d._access_for_user(u)
            if not level or level == 'none':
                continue
            if u.has_group('dms.group_dms_controller'):
                how = _("Document Controller")
            elif d.access_ids.filtered(lambda a: a.user_id == u):
                how = _("Given on this file")
            elif lines.filtered(lambda a: a.user_id == u):
                how = _("Folder %s", lines.filtered(lambda a: a.user_id == u)[:1].folder_id.complete_name)
            elif u == d.owner_id:
                how = _("Owner")
            else:
                how = _("Group")
            rows.append({'user_id': u.id, 'name': u.name, 'level': level, 'level_label': labels.get(level, level),
                         'how': how, 'owner': u == d.owner_id})
        rows.sort(key=lambda r: (-LEVEL_RANK.get(r['level'], 0), r['name']))
        return rows

    # ------------------------------------------------------------------ read-only datasets
    def _dataset(self, env, ctrl, user, controller, name):
        """Rows of one dataset, already filtered by what this user may see."""
        Doc = env['dms.document'].sudo()
        vis_cache = {}

        def vis(d):
            if d.id not in vis_cache:
                vis_cache[d.id] = self._vis(ctrl, d, user, controller) if d else 'hidden'
            return vis_cache[d.id]
        def dt(v):
            # the user's own clock: "today" means their today
            return fields.Datetime.context_timestamp(user, v).strftime('%Y-%m-%d %H:%M') if v else ''
        rows = []
        if name == 'documents':
            for d in Doc.search([('state', '!=', 'discarded')], limit=3000):
                v = vis(d)
                if v == 'hidden':
                    continue
                cur = d.current_revision_id
                row = {'id': d.id, 'code': d.code or '', 'title': d.name, 'state': d.state, 'type': d.type_id.name or '',
                       'folder': d.folder_id.complete_name or '', 'owner': d.owner_id.name or '',
                       'revision': cur.rev_label if cur else '', 'created': dt(d.create_date),
                       'published_on': fields.Date.to_string(cur.approved_date) if cur and cur.approved_date else '',
                       'expires': fields.Date.to_string(d.expiry_date) if d.expiry_date and v == 'open' else '',
                       'checked_out_by': d.locked_by_id.name or '', 'can_open': v == 'open',
                       'personal_data': ''}
                if v == 'open':
                    found = self._fields(d)
                    rev = d._insight_revision()
                    if rev and rev.has_sensitive:
                        row['personal_data'] = ', '.join(x['label'] for x in json.loads(rev.sensitive_json or '[]'))
                    row.update({'vendor': str(found.get('vendor') or found.get('supplier') or ''),
                                'buyer': str(found.get('buyer') or found.get('customer') or ''),
                                'amount': self._amount(found) or ''})
                rows.append(row)
        elif name == 'approvals':
            now = fields.Datetime.now()
            for a in env['dms.approval'].sudo().search([], order='id desc', limit=3000):
                d = a.revision_id.document_id
                if vis(d) != 'open':
                    continue
                r = a.revision_id
                rows.append({'id': d.id, 'document': d.display_name, 'revision': r.rev_label, 'level': a.level.upper(),
                             'approver': a.user_id.name, 'status': a.state, 'decided_on': dt(a.date),
                             'waiting_days': (now - r.submitted_date).days if a.state == 'waiting' and r.submitted_date else 0,
                             'comment': a.comment or ''})
        elif name == 'access_requests':
            for q in env['dms.access.request'].sudo().search([], order='id desc', limit=2000):
                if not (controller or q.user_id == user or q._can_decide(user)):
                    continue
                rows.append({'id': q.document_id.id, 'document': q.document_id.display_name, 'requested_by': q.user_id.name,
                             'level': q.level, 'status': q.state, 'requested_on': dt(q.create_date),
                             'decided_by': q.decided_by_id.name or ''})
        elif name == 'folders':
            Folder = env['dms.folder']
            counts = dict(Doc._read_group([('state', '!=', 'discarded')], ['folder_id'], ['__count']))
            for f in Folder._visible_folders(user):
                rows.append({'folder': f.complete_name, 'department': f.department_id.name or '', 'parent': f.parent_id.complete_name or '',
                             'l1_reviewer': f._resolve('reviewer_id').name or '', 'l2_approver': f._resolve('approver_id').name or '',
                             'l3_final': f._resolve('final_approver_id').name or '',
                             'my_access': dict(ACCESS_LEVELS).get(f._access_for_user(user), 'none') if f._access_for_user(user) else 'none',
                             'documents': counts.get(f, 0)})
        elif name == 'audit_log':
            if not controller:
                raise AccessError(_("Only the Document Controller can read the audit log."))
            labels = dict(env['dms.log']._fields['action']._description_selection(env))
            for l in env['dms.log'].sudo().search([], limit=3000):
                rows.append({'date': dt(l.date), 'user': l.user_id.name or '', 'action': l.action,
                             'action_label': labels.get(l.action, l.action), 'document': l.document_ref or '',
                             'id': l.document_id.id or False, 'details': l.details or ''})
        return rows

    def _run_data(self, env, ctrl, user, controller, spec):
        name = spec.get('dataset')
        if name not in DATASETS:
            raise UserError(_("I can only read documents, approvals, access requests, folders and the audit log."))
        rows = self._dataset(env, ctrl, user, controller, name)
        # words people use -> the dataset's own fields and values
        alias = {'created_by': 'owner', 'uploaded_by': 'owner', 'author': 'owner', 'creator': 'owner', 'user_id': 'owner',
                 'status': 'state', 'stage': 'state', 'name': 'title', 'number': 'code', 'folder_name': 'folder'}
        if name != 'documents':
            alias = {'stage': 'status', 'state': 'status', 'user_id': 'user', 'requested_user': 'requested_by'}
        me_words = {'me', 'my', 'mine', 'myself', 'self', 'current user', 'i', 'you', user.login.lower(), user.name.lower()}
        clean = []
        today = fields.Date.context_today(user)
        days = {'today': today, 'yesterday': today - timedelta(days=1),
                'monday': today - timedelta(days=today.weekday()), 'week': today - timedelta(days=today.weekday()),
                'month': today.replace(day=1)}
        for f in spec.get('filters') or []:
            if isinstance(f, (list, tuple)) and len(f) == 3 and isinstance(f[2], str) and '<' in f[2]:
                key = next((k for k in days if k in f[2].lower()), None)
                if key:
                    f = [f[0], f[1], fields.Date.to_string(days[key])]
            if not isinstance(f, (list, tuple)) or len(f) != 3:
                continue
            field, op, value = f
            field = alias.get(str(field), str(field))
            if isinstance(value, str) and value.strip().lower() in me_words:
                value = user.name
                op = '=' if op in ('contains', 'ilike', 'like') else op
            if field == 'state' and isinstance(value, str):
                v = value.strip().lower().replace(' ', '_')
                if v in ('unpublished', 'not_published'):
                    field, op, value = 'state', '!=', 'published'
                elif v in ('review', 'in_review', 'pending_approval', 'waiting'):
                    value = 'in_review'
                elif v in ('refused', 'rejected'):
                    value = 'rejected'
            clean.append([field, op, value])
        spec = dict(spec, filters=clean)

        def match(row, f):
            if not isinstance(f, (list, tuple)) or len(f) != 3:
                return True
            field, op, value = f
            have = row.get(field)
            if have is None:
                return True
            a, b = str(have).lower(), str(value).lower()
            try:
                if op in ('>', '<', '>=', '<='):
                    x, y = (float(have), float(value)) if not isinstance(have, str) else (a, b)
                    return {'>': x > y, '<': x < y, '>=': x >= y, '<=': x <= y}[op]
            except (TypeError, ValueError):
                return True
            if op in ('in', 'not in') and isinstance(value, (list, tuple)):
                hit = a in [str(x).lower() for x in value]
                return hit if op == 'in' else not hit
            if op == '=' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', b):
                return a.startswith(b)
            if op == '=':
                return a == b
            if op == '!=':
                return a != b
            return b in a  # contains
        for f in spec.get('filters') or []:
            rows = [r for r in rows if match(r, f)]
        total = len(rows)
        out = {'kind': 'table', 'dataset': name, 'total': total}
        group = spec.get('group_by')
        if group and rows and group in rows[0] and not spec.get('sum') \
                and len({str(r.get(group)) for r in rows}) >= 0.8 * len(rows):
            group = None  # grouping on a near-unique field ("list all folders") is just the list
        if group and rows and group in rows[0]:
            out['grouped'] = True
            agg = spec.get('sum') if spec.get('sum') and spec.get('sum') in rows[0] else None
            groups = OrderedDict()
            for r in rows:
                g = groups.setdefault(str(r.get(group) or '-'), {'count': 0, 'sum': 0.0})
                g['count'] += 1
                if agg:
                    try:
                        g['sum'] += float(r.get(agg) or 0)
                    except (TypeError, ValueError):
                        pass
            ordered = sorted(groups.items(), key=lambda kv: -kv[1]['count'])
            if agg and all(v['count'] == 1 for v in groups.values()):
                # one row per group: the sum is the answer (e.g. folders and their document counts)
                ordered = sorted(groups.items(), key=lambda kv: -kv[1]['sum'])
                out['columns'] = [group.replace('_', ' ').title(), agg.replace('_', ' ').title()]
                out['rows'] = [[k, int(v['sum']) if float(v['sum']).is_integer() else round(v['sum'], 2)] for k, v in ordered][:100]
                out['bars'] = [{'label': k, 'count': v['sum']} for k, v in ordered][:12]
                return out
            out['columns'] = [group.replace('_', ' ').title(), _("Count")] + ([agg.title()] if agg else [])
            out['rows'] = [[k, v['count']] + ([round(v['sum'], 2)] if agg else []) for k, v in ordered][:100]
            out['bars'] = [{'label': k, 'count': v['count']} for k, v in ordered][:12]
            return out
        order = spec.get('order')
        if order and rows and order in rows[0]:
            filled = [r for r in rows if r.get(order) not in (None, '', False)]
            empty = [r for r in rows if r.get(order) in (None, '', False)]
            filled.sort(key=lambda r: r.get(order), reverse=bool(spec.get('desc')))
            rows = filled + empty
        limit = max(1, min(int(spec.get('limit') or 50), 200))
        hidden = {'id', 'can_open'}
        cols = [c for c in (rows[0].keys() if rows else []) if c not in hidden]
        # keep the table readable: the most telling columns first
        prefer = {'documents': ['code', 'title', 'state', 'type', 'folder', 'owner', 'vendor', 'amount', 'published_on'],
                  'approvals': ['document', 'level', 'approver', 'status', 'waiting_days', 'decided_on', 'comment'],
                  'access_requests': ['document', 'requested_by', 'level', 'status', 'requested_on', 'decided_by'],
                  'folders': ['folder', 'department', 'l1_reviewer', 'l2_approver', 'l3_final', 'my_access', 'documents'],
                  'audit_log': ['date', 'user', 'action_label', 'document', 'details']}.get(name, cols)
        asked = [str(f[0]) for f in spec.get('filters') or [] if isinstance(f, (list, tuple)) and f]
        asked += [spec.get('order')] if spec.get('order') else []
        keep = [c for c in prefer if c in cols]
        for c in asked:
            if c in cols and c not in keep:
                keep.insert(2, c)  # the field asked about, right after the name
        cols = keep[:8]
        out['columns'] = [c.replace('_', ' ').title() for c in cols]
        out['rows'] = [['' if r.get(c) is None or r.get(c) is False else r.get(c) for c in cols] for r in rows[:limit]]
        out['ids'] = [r.get('id') or False for r in rows[:limit]]
        return out

    # ------------------------------------------------------------------ the route
    @http.route('/dms/assistant/ask', type='jsonrpc', auth='user')
    def ask(self, message='', history=None, image=None, context_doc_ids=None, session_id=None):
        res = self._ask(message, history, image, context_doc_ids)
        # keep the conversation: the user's own chat history, readable by Controllers in the back office
        env = _env()
        Session = env['dms.ai.session']
        session = Session.browse(int(session_id)).exists() if session_id else Session
        if session and session.user_id != env.user:
            session = Session
        if not session:
            title = _clip(message, 60) if message else _("Screenshot")
            session = Session.create({'name': title})
        img = image.split(',', 1)[1] if image and ',' in image else False
        env['dms.ai.message'].create([
            {'session_id': session.id, 'role': 'user', 'text': message, 'image': img},
            {'session_id': session.id, 'role': 'assistant', 'text': res['answer'], 'intent': res['intent'],
             'cards_json': json.dumps(res['cards'], default=str), 'suggestions_json': json.dumps(res['suggestions']),
             'focus_json': json.dumps(res['focus'])},
        ])
        session.last_at = fields.Datetime.now()
        res['session'] = {'id': session.id, 'name': session.name}
        return res

    @http.route('/dms/assistant/sessions', type='jsonrpc', auth='user')
    def sessions(self):
        env = _env()
        return [{'id': s.id, 'name': s.name, 'last_at': fields.Datetime.to_string(s.last_at), 'count': s.message_count}
                for s in env['dms.ai.session'].search([('user_id', '=', env.user.id)], limit=100)]

    @http.route('/dms/assistant/session', type='jsonrpc', auth='user')
    def session_load(self, session_id):
        env = _env()
        s = env['dms.ai.session'].search([('id', '=', int(session_id)), ('user_id', '=', env.user.id)])
        if not s:
            raise AccessError(_("This chat is not yours."))
        out = []
        for m in s.message_ids.sorted('id'):
            if m.role == 'user':
                out.append({'role': 'user', 'text': m.text or '', 'image': '/web/image/dms.ai.message/%s/image' % m.id if m.image else None})
            else:
                out.append({'role': 'assistant', 'answer': m.text or '', 'intent': m.intent,
                            'cards': json.loads(m.cards_json or '[]'), 'suggestions': json.loads(m.suggestions_json or '[]'),
                            'focus': json.loads(m.focus_json or '[]')})
        return {'id': s.id, 'name': s.name, 'messages': out}

    @http.route('/dms/assistant/session/rename', type='jsonrpc', auth='user')
    def session_rename(self, session_id, name):
        env = _env()
        s = env['dms.ai.session'].search([('id', '=', int(session_id)), ('user_id', '=', env.user.id)])
        name = (name or '').strip()[:120]
        if s and name:
            s.name = name
        return bool(s)

    @http.route('/dms/assistant/session/delete', type='jsonrpc', auth='user')
    def session_delete(self, session_id):
        env = _env()
        s = env['dms.ai.session'].search([('id', '=', int(session_id)), ('user_id', '=', env.user.id)])
        s.unlink()
        return True

    def _ask(self, message='', history=None, image=None, context_doc_ids=None):
        env = _env()
        user = env.user
        ctrl = DmsController()
        controller = user.has_group('dms.group_dms_controller')
        message = (message or '').strip()[:1500]
        history = (history or [])[-6:]
        context_ids = [int(i) for i in (context_doc_ids or []) if i]
        steps = []
        shot_text, shot_raw = '', None
        if image:
            shot_text, shot_raw = self._read_image(image)
            steps.append('read_image')
        if not message and not image:
            raise UserError(_("Ask something, or paste a screenshot."))

        language = detect_language(message) if message else 'english'
        plan_message = message
        if language in ('tanglish', 'tamil') and message:
            try:
                tr = ai_json(env, "Translate the user's Tamil or Tanglish question into plain English, keeping names, numbers and "
                                  "document numbers exactly. Answer only with JSON: {\"english\": \"...\"}", message, max_tokens=150)
                plan_message = (tr.get('english') if isinstance(tr, dict) else '') or message
            except UserError:
                pass
        types = env['dms.document.type'].sudo().search([]).mapped('name')
        convo = '\n'.join('%s: %s' % (h.get('role'), _clip(h.get('text'), 300)) for h in history)
        ctx_docs = env['dms.document'].sudo().browse(context_ids).exists()
        plan_user = "DOCUMENT TYPES: %s\nRECENTLY DISCUSSED: %s\nCONVERSATION:\n%s\n\nUSER: %s%s" % (
            ', '.join(types), ', '.join(ctx_docs.mapped('display_name')) or '-', convo or '-',
            plan_message or '(no text, only a screenshot)',
            ("\n\nSCREENSHOT TEXT:\n" + _clip(shot_text, 1500)) if shot_text else '')
        try:
            plan = ai_json(env, PLAN_PROMPT % {'today': fields.Date.context_today(user),
                                               'datasets': '\n'.join('- %s: %s' % kv for kv in DATASETS.items())},
                           plan_user, max_tokens=450)
        except UserError:
            plan = {'intent': 'find', 'search': message}
        if not isinstance(plan, dict):
            plan = {'intent': 'find', 'search': message}
        intent = plan.get('intent') or 'find'
        # "how many / evlo / ethana" is a count even when the planner says find
        if intent in ('greeting', 'other') and re.search(r'\?|enna|eppadi|epdi|yaar|how|what|who|why|where|எப்படி|என்ன|யார்', message, re.I) \
                and len(message.split()) > 2:
            intent = 'help'
        spec = plan.get('data') if isinstance(plan.get('data'), dict) else {}
        howto = re.search(r'\b(how|eppadi|epdi|explain|what is|what does|why)\b', plan_message or message or '', re.I)
        if intent in ('other', 'help', 'greeting') and spec.get('dataset') in DATASETS and \
                (spec.get('filters') or spec.get('group_by')) and not howto:
            intent = 'data'
        if intent in ('count', 'find', 'status', 'my_work') and spec.get('dataset') in DATASETS and (
                spec.get('group_by') or spec.get('filters') or spec.get('order')
                or re.search(r'\b(each|per|by|wise|vaariyaa|vaariya|every)\b', message, re.I)) \
                and not plan.get('party') and not plan.get('question') and not (intent == 'status' and plan.get('doc_ref')):
            intent = 'data'
        if intent == 'find' and re.search(r'how many|count|total|sum of|evlo|evvalavu|ethana|ethanai|எத்தனை|எவ்வளவு|மொத்த', message, re.I):
            intent = 'count'
        if image and intent not in ('password', 'request_access', 'status'):
            intent = 'identify'
        steps.append('understand')

        cards, facts, focus = [], {}, []
        rows_cache = {}

        def visible_rows(cands):
            rows = []
            for d, score, snip in cands:
                v = rows_cache.get(d.id) or self._vis(ctrl, d, user, controller)
                rows_cache[d.id] = v
                if v != 'hidden':
                    rows.append((d, v, score, snip))
            return rows

        if intent == 'identify':
            steps.append('match')
            matches = []
            for d, score, how in self._identify(env, shot_text or message, shot_raw):
                v = self._vis(ctrl, d, user, controller)
                if v != 'hidden':
                    matches.append((d, v, score, how))
            if matches:
                d, v, score, how = matches[0]
                card = self._record_card(ctrl, d, user, v)
                card['confidence'] = score
                card['match_how'] = how
                cards.append(card)
                focus = [d.id]
                facts = {'matched_document': d.display_name, 'confidence': score, 'you_can_open_it': v == 'open',
                         'stage': card['stage'], 'owner': d.owner_id.name, 'folder': d.folder_id.complete_name,
                         'your_access': card['my_level_label'],
                         'pending_request': card.get('request') or False,
                         'other_possible_matches': [m[0].display_name for m in matches[1:]]}
                if v == 'open':
                    facts.update({'summary': card.get('summary'), 'details': dict(card.get('grid', [])),
                                  'extracted_fields': dict(card.get('fields_table', [])),
                                  'risk_points': [r.get('point') for r in card.get('risks', [])],
                                  'revisions': card.get('revisions')})
                elif not card.get('request'):
                    cards.append({'kind': 'request', 'doc': ctrl._doc_card(d, user), 'level': 'view', 'reason': ''})
            else:
                words = len(re.findall(r'[A-Za-z0-9]{3,}', shot_text or ''))
                facts = {'matched_document': None,
                         'text_read_from_picture': _clip(shot_text, 300) if words else '(almost no text on the picture)',
                         'note': 'No stored document matches this picture closely enough (or it is somewhere this user cannot see). '
                                 'Say so honestly; do not guess a document. Suggest pasting a clearer screenshot of the page, or '
                                 'asking by document number or title.'}

        elif intent == 'document':
            steps.append('find')
            d = self._find_ref(env, plan.get('doc_ref') or message, context_ids)
            v = self._vis(ctrl, d, user, controller) if d else 'hidden'
            if d and v != 'hidden':
                card = self._record_card(ctrl, d, user, v)
                cards.append(card)
                focus = [d.id]
                facts = {'document': d.display_name, 'details': dict(card['grid']), 'you_can_open_it': v == 'open',
                         'approval_steps': [st['label'] + (' by ' + st['who'] if st['who'] else '') for st in card['steps']]}
                if v == 'open':
                    facts.update({'summary': card.get('summary'), 'extracted_fields': dict(card.get('fields_table', [])),
                                  'risk_points': [r.get('point') for r in card.get('risks', [])],
                                  'important_dates': card.get('dates'), 'revisions': card.get('revisions'),
                                  'personal_data_found': card.get('sensitive')})
                elif not card.get('request'):
                    cards.append({'kind': 'request', 'doc': ctrl._doc_card(d, user), 'level': 'view', 'reason': ''})
            else:
                facts = {'document': None, 'looked_for': plan.get('doc_ref') or message}

        elif intent == 'access_list':
            steps.append('find')
            d = self._find_ref(env, plan.get('doc_ref') or '', context_ids)
            v = self._vis(ctrl, d, user, controller) if d else 'hidden'
            if not d or v == 'hidden':
                facts = {'document': None, 'looked_for': plan.get('doc_ref')}
            elif not self._may_see_access(d, user):
                focus = [d.id]
                facts = {'document': d.display_name,
                         'not_allowed': 'Only the Document Controller, the owner and people with Edit on it can see who has access.'}
            else:
                focus = [d.id]
                rows = self._access_rows(d)
                want = plan.get('access_level') if plan.get('access_level') in ('view', 'download', 'edit') else None
                shown = [r for r in rows if r['level'] == want] if want else rows
                cards.append({'kind': 'access', 'doc': ctrl._doc_card(d, user), 'rows': shown, 'all': len(rows),
                              'filter': dict(ACCESS_LEVELS).get(want) if want else ''})
                facts = {'document': d.display_name, 'asked_level': dict(ACCESS_LEVELS).get(want) if want else 'any',
                         'people_with_that_level': [{'name': r['name'], 'level': r['level_label'], 'why': r['how']} for r in shown],
                         'everyone_with_access': [{'name': r['name'], 'level': r['level_label'], 'why': r['how']} for r in rows],
                         'your_own_access': dict(ACCESS_LEVELS).get(d._access_for_user(user), 'none'),
                         'note': 'If nobody has the asked level, say so and list who has which level. Do not suggest requesting access to someone who already has it.'}
                if want and not shown:
                    cards[-1]['rows'] = rows
                    cards[-1]['filter'] = ''
                    cards[-1]['none_with'] = dict(ACCESS_LEVELS).get(want)

        elif intent == 'data':
            steps.append('search')
            spec = plan.get('data') if isinstance(plan.get('data'), dict) else {}
            try:
                table = self._run_data(env, ctrl, user, controller, spec)
                table['title'] = message
                cards.append(table)
                facts = {'dataset': spec.get('dataset'), 'filters': spec.get('filters'), 'group_by': spec.get('group_by'),
                         'total_rows': table['total'], 'columns': table.get('columns'), 'first_rows': table.get('rows', [])[:25]}
            except (UserError, AccessError) as e:
                facts = {'not_possible': str(e)}

        elif intent == 'status':
            steps.append('find')
            d = self._find_ref(env, plan.get('doc_ref') or message, context_ids)
            v = self._vis(ctrl, d, user, controller) if d else 'hidden'
            if d and v != 'hidden':
                card = self._status_card(ctrl, d, user, v)
                cards.append(card)
                focus = [d.id]
                facts = {'document': d.display_name, 'stage': card['stage'], 'waiting_for': card['waiting'],
                         'owner': d.owner_id.name, 'folder': d.folder_id.complete_name, 'your_access': card['my_level_label'],
                         'checked_out_by': card.get('locked_by'), 'ai_check': card.get('ai'),
                         'steps': [s['label'] + (' by ' + s['who'] if s['who'] else '') for s in card['steps']]}
                if v == 'listed' and not card.get('request'):
                    cards.append({'kind': 'request', 'doc': ctrl._doc_card(d, user), 'level': 'view', 'reason': ''})
            else:
                facts = {'document': None, 'looked_for': plan.get('doc_ref') or message}

        elif intent == 'password':
            steps.append('find')
            d = self._find_ref(env, plan.get('doc_ref') or '', context_ids)
            ICP = env['ir.config_parameter'].sudo()
            protect = ICP.get_param('dms.download_protect') in ('True', '1')
            v = self._vis(ctrl, d, user, controller) if d else 'hidden'
            level = d._access_for_user(user) if d and v != 'hidden' else False
            own_pw = False
            if d and v == 'open':
                from ..models.dms_preview import is_password_protected
                rev = d.current_revision_id or d.revision_ids[:1]
                try:
                    own_pw = bool(rev) and rev._preview_kind() in ('pdf', 'doc', 'sheet') and \
                        is_password_protected(rev._read_data(), rev._ext())
                except Exception:  # noqa: BLE001
                    own_pw = False
            can_dl = LEVEL_RANK.get(level, 0) >= 2
            if not d or v == 'hidden':
                situation = 'No document found that you can see; ask which document.'
            elif own_pw:
                situation = 'The file itself was uploaded with its own password; the DMS does not know it: ask the person who uploaded it.'
            elif not protect:
                situation = 'Downloads are not password protected, so the downloaded file opens without a password.'
            elif can_dl:
                situation = 'Downloads are protected. The password for the downloaded file is shown in the card below (tap Show).'
            else:
                situation = 'Downloads are protected and this user has no download access: they can request View & Download access with the form below.'
            facts = {'document': d.display_name if d and v != 'hidden' else None, 'situation': situation,
                     'note': 'Opening the file inside the DMS never asks for a password.'}
            if d and v != 'hidden':
                focus = [d.id]
            if d and v == 'open' and protect and can_dl and not own_pw:
                cards.append({'kind': 'password', 'doc': ctrl._doc_card(d, user),
                              'password': ICP.get_param('dms.download_password') or '', 'own_password': own_pw})
                env['dms.log'].sudo().log('download', document=d, details="Download password shown by Ask DMS")
            elif d and v != 'hidden' and LEVEL_RANK.get(level, 0) < 2:
                cards.append({'kind': 'request', 'doc': ctrl._doc_card(d, user), 'level': 'download', 'reason': ''})

        elif intent == 'request_access':
            d = self._find_ref(env, plan.get('doc_ref') or '', context_ids)
            v = self._vis(ctrl, d, user, controller) if d else 'hidden'
            if d and v != 'hidden':
                focus = [d.id]
                have = d._access_for_user(user)
                want = plan.get('level') if plan.get('level') in ('view', 'download', 'edit') else 'view'
                mine = ctrl._my_request(d, user)
                if LEVEL_RANK.get(have, 0) >= LEVEL_RANK[want]:
                    facts = {'document': d.display_name, 'you_already_have': dict(ACCESS_LEVELS).get(have)}
                    cards.append(self._card(ctrl, d, user, v))
                elif mine and mine.get('state') == 'pending':
                    facts = {'document': d.display_name, 'request_already_waiting': True}
                    cards.append(self._card(ctrl, d, user, v))
                else:
                    facts = {'document': d.display_name, 'request_form_shown': True,
                             'note': 'The user confirms with the Send button; nothing was sent yet.'}
                    cards.append({'kind': 'request', 'doc': ctrl._doc_card(d, user), 'level': want,
                                  'reason': plan.get('reason') or ''})
            else:
                facts = {'document': None, 'looked_for': plan.get('doc_ref')}

        elif intent == 'my_work':
            steps.append('find')
            Approval = env['dms.approval'].sudo()
            mine_appr = Approval.search([('state', '=', 'waiting'), ('revision_id.state', '=', 'in_review'), ('user_id', '=', user.id)])
            mine_appr = mine_appr.filtered(lambda a: a == a.revision_id.approval_ids.filtered(lambda x: x.state == 'waiting').sorted('sequence')[:1])
            to_approve = mine_appr.revision_id.document_id
            Doc = env['dms.document'].sudo()
            drafts = Doc.search([('owner_id', '=', user.id), ('state', 'in', ('draft', 'rejected', 'in_review'))], limit=20)
            locked = Doc.search([('locked_by_id', '=', user.id)])
            reqs = env['dms.access.request'].sudo().search([('user_id', '=', user.id), ('state', '=', 'pending')])
            if to_approve:
                cards.append({'kind': 'docs', 'title': _("Waiting for your approval"), 'icon': 'fa-gavel',
                              'docs': [self._card(ctrl, d, user, 'open') for d in to_approve]})
            if drafts:
                cards.append({'kind': 'docs', 'title': _("Your documents not published yet"), 'icon': 'fa-pencil',
                              'docs': [self._card(ctrl, d, user, 'open') for d in drafts]})
            if locked:
                cards.append({'kind': 'docs', 'title': _("Checked out by you"), 'icon': 'fa-lock',
                              'docs': [self._card(ctrl, d, user, 'open') for d in locked]})
            facts = {'to_approve': to_approve.mapped('display_name'),
                     'my_unpublished': ['%s (%s)' % (d.display_name, d.state) for d in drafts],
                     'checked_out_by_me': locked.mapped('display_name'),
                     'my_pending_access_requests': reqs.mapped('document_id.display_name')}

        elif intent in ('help', 'greeting', 'other'):
            facts = {'answer_this_question_from_the_guide': message}

        else:  # find / count
            steps.append('search')
            rows = visible_rows(self._candidates(env, plan))
            focus = [d.id for d, *_r in rows[:5]]
            if intent == 'count' or len(rows) > 1:
                stats = self._stats_card([(d, v) for d, v, *_r in rows])
                stats['title'] = plan.get('search') or plan.get('party') or ''
                cards.append(stats)
            if rows:
                cards.append({'kind': 'docs', 'title': _("Documents"), 'icon': 'fa-files-o',
                              'docs': [self._card(ctrl, d, user, v, score, snip) for d, v, score, snip in rows[:12]],
                              'more': max(0, len(rows) - 12)})
            open_rows = [(d, snip) for d, v, _s, snip in rows if v == 'open']
            facts = {'found': len(rows), 'you_can_open': len(open_rows),
                     'need_access': sum(1 for _d, v, *_r in rows if v == 'listed'),
                     'filters': {k: plan.get(k) for k in ('type', 'party', 'date_from', 'date_to', 'state') if plan.get(k)}}
            if intent == 'count' and cards and cards[0].get('kind') == 'stats':
                amt = cards[0]['amount']
                if amt:
                    whole = '{:,.0f}'.format(amt)
                    # Indian grouping: 1,20,000
                    digits = whole.replace(',', '')
                    indian = digits[-3:]
                    rest = digits[:-3]
                    while rest:
                        indian = rest[-2:] + ',' + indian
                        rest = rest[:-2]
                    facts['total_amount_of_documents_you_can_open'] = '%s %s' % ('₹' if (cards[0]['currency'] or 'INR').upper() in ('INR', '₹', 'RS') else cards[0]['currency'], indian)
            if plan.get('question') and open_rows:
                # content answer: only from documents this user may open
                facts['content'] = [{'document': d.display_name, 'fields': self._fields(d),
                                     'text': _clip(snip or (d._insight_revision().plain_text or ''), 900)}
                                    for d, snip in open_rows[:4]]
            facts['documents'] = [{'name': d.display_name, 'can_open': v == 'open', 'folder': d.folder_id.complete_name}
                                  for d, v, *_r in rows[:12]]

        steps.append('answer')
        if intent == 'password':
            # never let the AI write about the password itself: fixed wording, the value lives only in the card
            ta = language in ('tamil', 'tanglish')
            name = facts.get('document') or ''
            texts = {
                'card': ("**%s** download panna file-ku password keezha irukku, **Show** press pannunga. DMS-kulla open panna password thevai illa." % name)
                if ta else ("The password for the downloaded copy of **%s** is in the card below; press **Show**. Opening it inside the DMS never needs a password." % name),
                'request': ("**%s** download panna ungalukku access illa. Keezha **View & Download** access request anuppunga." % name)
                if ta else ("You don't have download access to **%s** yet. Send a View & Download request below." % name),
                'other': facts.get('situation') or '',
            }
            kinds = [c['kind'] for c in cards]
            answer = texts['card'] if 'password' in kinds else texts['request'] if 'request' in kinds else texts['other']
            env['dms.log'].sudo().log('ai_search', details=_clip("Ask DMS: %s" % message, 250))
            return {'answer': answer, 'cards': cards, 'suggestions': [], 'intent': intent, 'focus': focus,
                    'steps': steps, 'shot_text': ''}
        try:
            guide = ("\n\nGUIDE:\n" + kb_for(plan_message or message)) if intent in ('help', 'greeting', 'other', 'access_list') else ''
            reply = ai_json(env, ANSWER_PROMPT % {'language': language},
                            "USER QUESTION: %s\nINTENT: %s\nFACTS: %s%s\n\nREPLY LANGUAGE: %s" % (
                                message or '(screenshot)', intent, json.dumps(facts, default=str, ensure_ascii=False)[:6000], guide,
                                {'tanglish': ("Tanglish = everyday spoken Tamil written in English letters, with the technical words in English. Keep sentences simple. "
                                             "Examples: 'Indha document **ADMINISTRATION** folder-la irukku. Idhu innum Draft-la irukku, approval-ku anuppala.' "
                                             "'Ungalukku **Edit** access irukku, so neenga check out panni edit pannalaam.' "
                                             "'Indha document-ku View access mattum yaarukkum illa; Administrator-ku Edit access irukku.' "
                                             "Use only Tamil and English words, never other languages; tables and headings can stay in English."),
                                 'tamil': "Tamil script.", 'english': "English only."}[language]),
                            max_tokens=1100)
        except UserError:
            reply = {}
        answer = (reply.get('answer') if isinstance(reply, dict) else '') or _("Here is what I found.")
        if any(c.get('kind') in ('table', 'access', 'stats', 'docs', 'record') for c in cards):
            # the card already shows the rows: no second copy of the table in the text
            answer = re.sub(r'(?m)^\s*\|.*\|\s*$\n?', '', answer)
            answer = re.sub(r'\n{3,}', '\n\n', answer).strip() or _("Here it is.")
        if language == 'tanglish' and detect_language(re.sub(r'\|.*\|', ' ', answer)) != 'tanglish':
            # long structured answers drift into English: rewrite them in Tanglish, structure kept
            try:
                fixed = ai_json(env, "Rewrite the text in Tanglish (everyday spoken Tamil in English letters, technical words stay in English). "
                                     "Keep every Markdown heading, list, table, number, name and **bold** exactly. Only Tamil and English words. "
                                     "Example style: 'Indha document ADMINISTRATION folder-la irukku. Approval-ku anuppina apram L1 reviewer-ku pogum.' "
                                     "Answer only with JSON: {\"text\": \"<rewritten text>\"}",
                                answer, max_tokens=1300)
                if isinstance(fixed, dict) and len(fixed.get('text') or '') > len(answer) * 0.5:
                    answer = fixed['text']
            except UserError:
                pass
        suggestions = [s for s in (reply.get('suggestions') or []) if isinstance(s, str)][:4] if isinstance(reply, dict) else []
        if intent == 'help' or intent == 'greeting':
            cards = cards or []
        env['dms.log'].sudo().log('ai_search', details=_clip("Ask DMS: %s" % (message or 'screenshot'), 250))
        return {'answer': answer, 'cards': cards, 'suggestions': suggestions, 'intent': intent, 'plan': plan,
                'focus': focus, 'steps': steps, 'shot_text': _clip(shot_text, 300) if shot_text else ''}

    @http.route('/dms/assistant/starters', type='jsonrpc', auth='user')
    def starters(self):
        """Personal suggestions for the empty chat."""
        env = _env()
        user = env.user
        Approval = env['dms.approval'].sudo()
        waiting = Approval.search_count([('state', '=', 'waiting'), ('revision_id.state', '=', 'in_review'), ('user_id', '=', user.id)])
        mine = env['dms.document'].sudo().search([('owner_id', '=', user.id), ('state', '!=', 'discarded')], order='write_date desc', limit=1)
        party = ''
        inv = env['dms.document'].sudo().search([('type_id.name', 'ilike', 'invoice'), ('ai_index', '!=', False)], limit=1)
        if inv:
            m = re.search(r'buyer: ([^\n]+?)(?: [a-z_]+:|\n|$)', inv.ai_index or '')
            party = (m.group(1).split()[0:2] and ' '.join(m.group(1).split()[0:2])) if m else ''
        out = []
        if waiting:
            out.append({'icon': 'fa-gavel', 'text': _("What is waiting for my approval?")})
        out.append({'icon': 'fa-inbox', 'text': _("What is waiting on me?")})
        if mine:
            out.append({'icon': 'fa-map-marker', 'text': _("What stage is %s in?", mine.name)})
        out.append({'icon': 'fa-bar-chart', 'text': _("How many invoices %s?", ('related to ' + party) if party else 'do we have')})
        out.append({'icon': 'fa-search', 'text': _("Find documents about payment terms")})
        out.append({'icon': 'fa-question-circle', 'text': _("Who can see the Approve button?")})
        out.append({'icon': 'fa-key', 'text': _("A downloaded file asks for a password")})
        return {'name': user.name.split()[0], 'starters': out[:6]}
