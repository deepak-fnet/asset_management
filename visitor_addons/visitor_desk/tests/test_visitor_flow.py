import base64
import io
from datetime import timedelta

from PIL import Image

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


def _jpeg(color='red'):
    buf = io.BytesIO()
    Image.new('RGB', (64, 64), color).save(buf, 'JPEG')
    return base64.b64encode(buf.getvalue()).decode()


class VisitorDeskCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reception = new_test_user(cls.env, 'vd_reception', groups='visitor_desk.group_visitor_reception')
        cls.security = new_test_user(cls.env, 'vd_security', groups='visitor_desk.group_visitor_security')
        cls.host = new_test_user(cls.env, 'vd_host', groups='base.group_user')
        cls.Visit = cls.env['visitor.visit'].with_user(cls.reception)
        cls.SecVisit = cls.env['visitor.visit'].with_user(cls.security)

    def _register(self, check_in=False, **vals):
        values = {'visitor_name': 'Kumar', 'mobile': '+91 70123 45678', 'company_name': 'ABC Technologies',
                  'purpose_id': self.env.ref('visitor_desk.purpose_meeting').id, 'host_id': self.host.id}
        values.update(vals)
        return self.Visit.desk_register(values, check_in)


@tagged('post_install', '-at_install')
class TestVisitorFlow(VisitorDeskCommon):

    def test_register_scan_in_out(self):
        card = self._register(photo=_jpeg(), photo_consent=True)
        visit = self.env['visitor.visit'].browse(card['id'])
        self.assertEqual(visit.state, 'registered')
        self.assertTrue(visit.name.startswith('VIS/'))
        self.assertTrue(visit.visitor_id.photo, 'visitor card keeps the latest face')

        res = self.SecVisit.desk_scan(card['pass_token'].lower())
        self.assertEqual(res['status'], 'checked_in')
        self.assertEqual(visit.check_in_by, self.security)
        self.assertIn(self.host.partner_id, visit.message_ids.partner_ids, 'host is notified on check-in')

        # an immediate second scan is ignored (double scan at the gate)
        self.assertEqual(self.SecVisit.desk_scan(visit.name)['status'], 'too_soon')
        self.assertEqual(visit.state, 'checked_in')

        visit.check_in = fields.Datetime.now() - timedelta(minutes=5)
        res = self.SecVisit.desk_scan(visit.name)
        self.assertEqual(res['status'], 'checked_out')
        self.assertTrue(visit.check_out)
        self.assertEqual(self.SecVisit.desk_scan(visit.name)['status'], 'checked_out')

    def test_returning_visitor_reuses_card(self):
        first = self.env['visitor.visit'].browse(self._register(check_in=True)['id'])
        first.with_user(self.security).action_check_out()
        found = self.env['visitor.visitor'].with_user(self.reception).lookup_by_mobile('70123-45678')
        self.assertEqual(found['id'], first.visitor_id.id)
        self.assertEqual(found['last_host_id'], self.host.id)
        second = self.env['visitor.visit'].browse(self._register(mobile='7012345678')['id'])
        self.assertEqual(second.visitor_id, first.visitor_id)
        self.assertEqual(first.visitor_id.visit_count, 2)

    def test_no_second_registration_while_active(self):
        visit = self.env['visitor.visit'].browse(self._register(check_in=True)['id'])
        found = self.env['visitor.visitor'].with_user(self.reception).lookup_by_mobile('7012345678')
        self.assertEqual(found['active_visit']['id'], visit.id)
        self.assertIn('already inside', found['active_message'])
        with self.assertRaises(ValidationError) as cm:
            self._register()
        self.assertIn('already inside', str(cm.exception))

        visit.with_user(self.security).action_check_out()
        waiting = self.env['visitor.visit'].browse(self._register()['id'])
        with self.assertRaises(ValidationError) as cm:
            self._register()
        self.assertIn('already registered', str(cm.exception))
        waiting.action_cancel()
        self.assertTrue(self._register()['id'], 'a new pass is allowed once the old one is closed')

    def test_closed_visit_is_locked(self):
        visit = self.env['visitor.visit'].browse(self._register(check_in=True)['id'])
        with self.assertRaises(UserError):
            visit.with_user(self.reception).action_cancel()  # inside: must check out instead
        visit.with_user(self.security).action_check_out()
        with self.assertRaises(UserError):
            visit.with_user(self.reception).write({'visitor_name': 'Someone else'})
        visit.write({'visitor_name': 'Corrected by admin'})  # admin may fix history
        self.assertEqual(visit.visitor_name, 'Corrected by admin')

    def test_pass_expires_next_day(self):
        visit = self.env['visitor.visit'].browse(self._register()['id'])
        visit.registered_on = fields.Datetime.now() - timedelta(days=1, hours=1)
        self.assertEqual(self.SecVisit.desk_scan(visit.pass_token)['status'], 'expired')
        with self.assertRaises(UserError):
            visit.with_user(self.security).action_check_in()
        self.env['visitor.visit']._cron_expire_passes()
        self.assertEqual(visit.state, 'cancelled')

    def test_configurable_purpose(self):
        Purpose = self.env['visitor.purpose']
        audit = Purpose.create({'name': 'Audit', 'icon': 'fa-clipboard', 'color': '#0f766e', 'default_hours': 3})
        self.assertIn('Audit', [p['name'] for p in Purpose.with_user(self.security).desk_purposes()])
        visit = self.env['visitor.visit'].browse(self._register(purpose_id=audit.id)['id'])
        self.assertEqual(visit._pass_colors()[0], '#0f766e')
        self.assertEqual(visit._desk_card()['purpose_icon'], 'fa-clipboard')
        with self.assertRaises(ValidationError):
            audit.unlink()  # in use: archive instead
        audit.active = False
        self.assertNotIn('Audit', [p['name'] for p in Purpose.desk_purposes()])
        with self.assertRaises(ValidationError):
            Purpose.create({'name': 'Bad colour', 'icon': 'fa-star', 'color': 'red'})
        with self.assertRaises(ValidationError):
            Purpose.create({'name': 'Bad icon', 'icon': 'star', 'color': '#000000'})
        with self.assertRaises(AccessError):
            Purpose.with_user(self.reception).create({'name': 'Nope', 'icon': 'fa-star', 'color': '#000000'})

    def test_validations(self):
        with self.assertRaises(ValidationError):
            self._register(mobile='12345')
        with self.assertRaises(ValidationError):
            self._register(photo=_jpeg())  # no consent

    def test_blocked_visitor(self):
        visit = self.env['visitor.visit'].browse(self._register()['id'])
        visit.visitor_id.write({'is_blocked': True, 'block_reason': 'Banned'})
        self.assertEqual(self.SecVisit.desk_scan(visit.pass_token)['status'], 'blocked')
        with self.assertRaises(UserError):
            visit.with_user(self.security).action_check_in()

    def test_security_cannot_register(self):
        with self.assertRaises(AccessError):
            self.SecVisit.desk_register({'visitor_name': 'X', 'mobile': '9999999999', 'purpose_id': self.env.ref('visitor_desk.purpose_other').id})

    def test_desk_data_and_overstay(self):
        visit = self.env['visitor.visit'].browse(self._register(check_in=True, expected_hours=1)['id'])
        visit.check_in = fields.Datetime.now() - timedelta(hours=2)
        kpi = self.Visit.desk_data()['kpi']
        self.assertEqual(kpi['inside'], 1)
        self.assertEqual(kpi['overstay'], 1)

    def test_pass_report_and_purge(self):
        visit = self.env['visitor.visit'].browse(self._register(photo=_jpeg(), photo_consent=True)['id'])
        html = self.env['ir.actions.report']._render_qweb_html('visitor_desk.action_report_visitor_pass', visit.ids)[0]
        self.assertIn(visit.pass_token.encode(), html)
        self.assertIn(b'data:image/png;base64', html)

        visit.with_user(self.security).action_check_in()
        visit.with_user(self.security).action_check_out()
        self.env['ir.config_parameter'].set_param('visitor_desk.photo_retention_days', '1')
        old = fields.Datetime.now() - timedelta(days=5)
        visit.registered_on = old
        visit.visitor_id.last_visit_date = old
        self.env['visitor.visit']._cron_purge_photos()
        self.assertFalse(visit.photo)
        self.assertFalse(visit.visitor_id.photo)


@tagged('post_install', '-at_install')
class TestVisitorDeskUi(HttpCase):

    def test_desk_loads(self):
        reception = new_test_user(self.env, 'vd_ui', name='Priya Front Desk',
                                  groups='visitor_desk.group_visitor_reception')
        Visit = self.env['visitor.visit'].with_user(reception)
        colors = ['#c08457', '#8d5524', '#e0ac69', '#f1c27d']
        for i, (name, company, purpose) in enumerate([
            ('Kumar', 'ABC Technologies', 'meeting'),
            ('Anitha Raman', 'Zenith Logistics', 'delivery'),
            ('Vikram S', 'Freelance', 'interview'),
            ('Meena K', 'Sri Power Services', 'service'),
        ]):
            Visit.desk_register({'visitor_name': name, 'company_name': company,
                                 'purpose_id': self.env.ref('visitor_desk.purpose_' + purpose).id,
                                 'mobile': '60000000%02d' % i, 'host_id': self.env.ref('base.user_admin').id,
                                 'photo': _jpeg(colors[i]), 'photo_consent': True}, i < 3)
        self.browser_js(
            '/odoo/action-visitor_desk.action_visitor_desk',
            """
            const wait = () => new Promise(r => setTimeout(r, 300));
            (async () => {
                for (let i = 0; i < 40 && document.querySelectorAll('.o_vd_card').length < 3; i++) { await wait(); }
                if (!document.querySelector('.o_vd_register')) { throw new Error('register panel missing'); }
                const desk = document.querySelector('.o_vd_desk');
                desk.scrollTop = 400;
                if (desk.scrollHeight > desk.clientHeight && desk.scrollTop === 0) {
                    throw new Error('front desk does not scroll');
                }
                console.log('test successful');
            })();
            """,
            login='vd_ui',
        )
        visit = self.env['visitor.visit'].search([('visitor_name', '=', 'Kumar')], limit=1)
        self.browser_js(
            f'/odoo/action-visitor_desk.action_visitor_visit/{visit.id}',
            """
            (async () => {
                for (let i = 0; i < 40 && !document.querySelector('.o_vd_face_field img'); i++) {
                    await new Promise(r => setTimeout(r, 300));
                }
                if (!document.querySelector('.o_vd_face_field img')) { throw new Error('face widget missing'); }
                console.log('test successful');
            })();
            """,
            login='vd_ui',
        )
