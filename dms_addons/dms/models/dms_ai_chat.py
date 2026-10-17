"""Ask DMS conversations: every chat is kept, so people can come back to it, rename it or delete it,
and Document Controllers can read them in Configuration > AI Chat History."""
import json
import re

from markupsafe import Markup, escape


def md_html(text):
    """The assistant's Markdown (headings, lists, tables, bold) as safe HTML for the back office."""
    def inline(t):
        t = str(escape(t))
        t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
        return re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
    lines, out, i = (text or '').replace('\r', '').split('\n'), [], 0
    cell = 'padding:6px 10px;border:1px solid #e3d4b8;text-align:left'
    while i < len(lines):
        line = lines[i]
        if re.match(r'^\s*\|.*\|\s*$', line):
            rows = []
            while i < len(lines) and re.match(r'^\s*\|.*\|\s*$', lines[i]):
                if not (re.fullmatch(r'[\s|:-]+', lines[i]) and '-' in lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            if rows:
                head = ''.join('<th style="%s;background:#f5eee0">%s</th>' % (cell, inline(c)) for c in rows[0])
                body = ''.join('<tr>%s</tr>' % ''.join('<td style="%s">%s</td>' % (cell, inline(c)) for c in r) for r in rows[1:])
                out.append('<table style="border-collapse:collapse;margin:6px 0;font-size:13px"><tr>%s</tr>%s</table>' % (head, body))
            continue
        m = re.match(r'^(#{1,4})\s+(.*)$', line)
        if m:
            out.append('<div style="font-weight:800;margin:10px 0 4px">%s</div>' % inline(m.group(2)))
        elif re.match(r'^\s*([-*•]|\d+[.)])\s+', line):
            items, ordered = [], bool(re.match(r'^\s*\d', line))
            while i < len(lines) and re.match(r'^\s*([-*•]|\d+[.)])\s+', lines[i]):
                items.append('<li>%s</li>' % inline(re.sub(r'^\s*([-*•]|\d+[.)])\s+', '', lines[i])))
                i += 1
            out.append('<%s style="margin:4px 0 8px;padding-left:22px">%s</%s>' % ('ol' if ordered else 'ul', ''.join(items), 'ol' if ordered else 'ul'))
            continue
        elif line.strip():
            out.append('<div>%s</div>' % inline(line))
        i += 1
    return Markup(''.join(out))

from odoo import api, fields, models, _


class DmsAiSession(models.Model):
    _name = 'dms.ai.session'
    _description = 'Ask DMS conversation'
    _order = 'last_at desc, id desc'

    name = fields.Char("Subject", required=True, default=lambda s: _("New chat"))
    user_id = fields.Many2one('res.users', "Asked by", required=True, default=lambda s: s.env.user, index=True,
                              ondelete='cascade')
    message_ids = fields.One2many('dms.ai.message', 'session_id', "Messages")
    message_count = fields.Integer("Questions", compute='_compute_counts', store=True)
    last_at = fields.Datetime("Last message", default=fields.Datetime.now, index=True)
    first_question = fields.Char(compute='_compute_counts', store=True)
    transcript = fields.Html("Conversation", compute='_compute_transcript', sanitize=False)

    @api.depends('message_ids.role', 'message_ids.text')
    def _compute_counts(self):
        for s in self:
            asked = s.message_ids.filtered(lambda m: m.role == 'user').sorted('id')
            s.message_count = len(asked)
            s.first_question = (asked[:1].text or '')[:200] if asked else ''

    def _compute_transcript(self):
        """The whole chat as readable HTML for the back office."""
        for s in self:
            parts = []
            for m in s.message_ids.sorted('id'):
                when = fields.Datetime.context_timestamp(s, m.create_date).strftime('%d %b %Y, %H:%M') if m.create_date else ''
                if m.role == 'user':
                    img = Markup('<div><img src="/web/image/dms.ai.message/%s/image" style="max-width:280px;max-height:200px;'
                                 'border-radius:10px;margin-bottom:6px;border:2px solid rgba(255,255,255,.35)"/></div>') % m.id \
                        if m.image else Markup('')
                    parts.append(Markup(
                        '<div style="display:flex;justify-content:flex-end;margin:14px 0">'
                        '<div style="max-width:70%%;background:#5a3a17;color:#fff;padding:10px 14px;border-radius:16px 16px 4px 16px">'
                        '%s<div style="white-space:pre-wrap">%s</div>'
                        '<div style="font-size:11px;opacity:.7;margin-top:4px;text-align:right">%s · %s</div></div></div>'
                    ) % (img, escape(m.text or ''), escape(s.user_id.name), when))
                else:
                    cards = ''
                    try:
                        kinds = [c.get('kind') for c in json.loads(m.cards_json or '[]')]
                        if kinds:
                            cards = Markup('<div style="margin-top:8px">%s</div>') % Markup('').join(
                                Markup('<span style="display:inline-block;font-size:11px;font-weight:700;padding:2px 9px;'
                                       'border-radius:10px;background:#efe4cf;color:#6a4119;margin-right:5px">%s</span>') % escape(k)
                                for k in kinds)
                    except ValueError:
                        pass
                    parts.append(Markup(
                        '<div style="display:flex;gap:10px;margin:14px 0;align-items:flex-start">'
                        '<div style="width:30px;height:30px;border-radius:50%%;flex:none;color:#fff;text-align:center;line-height:30px;'
                        'background:radial-gradient(circle at 30%% 25%%,#9fc0ff,#6a5ae0 45%%,#2c2a6b)">✦</div>'
                        '<div style="flex:1;background:#fff;border:1px solid #e3d4b8;border-radius:14px;padding:12px 16px">'
                        '<div style="line-height:1.6">%s</div>%s'
                        '<div style="font-size:11px;color:#8a6f4a;margin-top:6px">Ask DMS · %s%s</div></div></div>'
                    ) % (md_html(m.text or ''), cards, when, (' · ' + m.intent) if m.intent else ''))
            s.transcript = Markup('<div style="max-width:980px;font-size:14px">%s</div>') % Markup('').join(parts) \
                if parts else Markup('<p style="color:#8a6f4a">No messages.</p>')


class DmsAiMessage(models.Model):
    _name = 'dms.ai.message'
    _description = 'Ask DMS message'
    _order = 'id'

    session_id = fields.Many2one('dms.ai.session', required=True, ondelete='cascade', index=True)
    role = fields.Selection([('user', 'Question'), ('assistant', 'Answer')], required=True)
    text = fields.Text()
    image = fields.Binary(attachment=True)
    cards_json = fields.Text()
    suggestions_json = fields.Text()
    focus_json = fields.Text()
    intent = fields.Char()
