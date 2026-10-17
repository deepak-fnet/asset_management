"""Meaning search for the DMS: the document texts go to a vector index (ChromaDB behind the
ERP AI Flask service, /dms/* endpoints). Odoo stays the source of truth and checks access;
the index only answers "which documents mean the closest to this question".

Nothing here ever blocks the work: when the service is down, the assistant falls back to
the keyword search and indexing catches up with "Rebuild meaning search".
"""
import json
import logging
import threading

import requests

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)

TIMEOUT = 60


def _url(env):
    return (env['ir.config_parameter'].sudo().get_param('dms.vector_url') or 'http://localhost:9010').rstrip('/')


def vector_post(env, path, payload, timeout=TIMEOUT):
    token = env['ir.config_parameter'].sudo().get_param('dms.vector_token')
    headers = {'Authorization': 'Bearer %s' % token} if token else {}
    r = requests.post(_url(env) + path, json=payload, timeout=timeout, headers=headers)
    r.raise_for_status()
    return r.json()


def vector_search(env, query, k=20, doc_ids=None):
    """[{doc_id, score, text, ...}] best first; [] when the service is not reachable."""
    try:
        payload = {'query': query, 'k': k}
        if doc_ids:
            payload['doc_ids'] = list(doc_ids)
        return vector_post(env, '/dms/search', payload, timeout=30).get('results') or []
    except Exception as e:  # noqa: BLE001
        _logger.warning("DMS meaning search unavailable: %s", e)
        return []


class DmsDocumentVector(models.Model):
    _inherit = 'dms.document'

    vector_indexed = fields.Datetime("Meaning search updated", readonly=True, copy=False)

    def _vector_item(self):
        """What the index stores for one document: its text plus the facts people ask about."""
        self.ensure_one()
        rev = self._insight_revision()
        found = {}
        try:
            found = json.loads(rev.ai_fields_json or '{}') if rev else {}
        except ValueError:
            pass
        review = json.loads(rev.ai_review or 'null') or {} if rev else {}
        facts = '\n'.join('%s: %s' % (k.replace('_', ' '), v) for k, v in found.items() if v)
        text = '\n\n'.join(filter(None, [
            self.description or '', facts, review.get('summary') or '', rev.plain_text if rev else '',
        ]))
        return {'doc_id': self.id, 'rev_id': rev.id if rev else 0, 'code': self.code or '', 'title': self.name,
                'type': self.type_id.name or '', 'folder': self.folder_id.complete_name or '', 'text': text}

    def _vector_push(self):
        for d in self:
            try:
                vector_post(self.env, '/dms/index/upsert', d._vector_item())
                d.sudo().vector_indexed = fields.Datetime.now()
            except Exception as e:  # noqa: BLE001
                _logger.warning("DMS meaning search: %s not indexed: %s", d.display_name, e)

    def _vector_schedule(self):
        """Index after the transaction commits, in the background."""
        if not self:
            return
        dbname, ids = self.env.cr.dbname, self.ids

        def run():
            try:
                with Registry(dbname).cursor() as cr:
                    api.Environment(cr, SUPERUSER_ID, {})['dms.document'].browse(ids).exists()._vector_push()
            except Exception:  # noqa: BLE001
                _logger.exception("DMS meaning search: background indexing failed")

        self.env.cr.postcommit.add(lambda: threading.Thread(target=run, name='dms-vector', daemon=True).start())

    @api.model
    def _vector_rebuild(self):
        """Every live document, from scratch."""
        docs = self.sudo().search([('state', '!=', 'discarded')])
        items = [d._vector_item() for d in docs]
        res = vector_post(self.env, '/dms/index/sync', {'items': items}, timeout=900)
        docs.write({'vector_indexed': fields.Datetime.now()})
        return res

    def action_move(self, folder):
        res = super().action_move(folder)
        self._vector_schedule()
        return res

    def action_retire(self, reason=None):
        res = super().action_retire(reason)
        self._vector_schedule()
        return res

    def action_delete_permanently(self):
        ids = self.ids
        res = super().action_delete_permanently()
        try:
            vector_post(self.env, '/dms/index/delete', {'doc_ids': ids}, timeout=15)
        except Exception as e:  # noqa: BLE001
            _logger.warning("DMS meaning search: delete not sent: %s", e)
        return res


class DmsRevisionVector(models.Model):
    _inherit = 'dms.revision'

    def _build_insights(self):
        res = super()._build_insights()
        # the text is read now: send it to the meaning search (this already runs in the background)
        self.document_id._vector_push()
        return res

    def action_approve(self, comment=None):
        res = super().action_approve(comment)
        if self.state == 'current':
            self.document_id._vector_schedule()
        return res


class ResConfigSettingsVector(models.TransientModel):
    _inherit = 'res.config.settings'

    dms_vector_url = fields.Char("Meaning search service", config_parameter='dms.vector_url',
                                 default='http://localhost:9010',
                                 help="AI Search Service (ai_search_service, ChromaDB). Default http://localhost:9010.")
    dms_vector_token = fields.Char("Meaning search token", config_parameter='dms.vector_token',
                                   help="Only if the service was started with AI_SEARCH_TOKEN.")

    def action_dms_vector_rebuild(self):
        self.execute()
        try:
            res = self.env['dms.document']._vector_rebuild()
        except Exception as e:  # noqa: BLE001
            raise UserError(_("The meaning search service did not answer: %s", e))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'type': 'success', 'title': _("Meaning search rebuilt"),
                           'message': _("%(d)s documents, %(c)s pieces of text indexed.",
                                        d=res.get('documents'), c=res.get('chunks'))}}
