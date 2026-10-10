import base64
import difflib
import io
import json
import logging
import re

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

AI_FIELDS = ('contact_name', 'job_position', 'company_name', 'email', 'phone', 'mobile',
             'website', 'street', 'street2', 'city', 'zip')

AI_SYSTEM_PROMPT = """You extract contact details from the OCR text of a business/visiting card. The OCR text is noisy.
Rules:
- Fix obvious OCR errors: letters inside phone numbers (S->5, O->0, l/I->1, B->8, Z->2), missing dots in emails and domains (e.g. 'rilcom' -> 'ril.com'), spaces inside email addresses, 'l' misread as 'i' in names.
- The company logo/wordmark, slogans and taglines are NOT the person's name. contact_name must be a person's name.
- company_name: the full legal company name if printed (e.g. 'Reliance Industries Limited'), otherwise the brand name. Remove stray symbols.
- Ignore icon symbols such as @ © G ® at the start of lines.
- mobile: mobile numbers (India: 10 digits starting 6-9, often with +91). phone: landline/office numbers. Never put the same number in both.
- You may receive several OCR passes of the same card; combine them and prefer the reading that looks most correct.
- Use the email domain to cross-check and correct the company name, email and website spelling.
- If an email is visible but garbled (e.g. 'vikram smahmibosch.com' for Vikram Singh at Bosch), reconstruct the most likely address from the fragments, the person's name and the company domain (-> 'vikram.singh@bosch.com'). If no email fragment appears at all, leave email "".
- Website must be a clean domain like 'www.company.com' with no spaces; otherwise "".
- A heading like '05. Bosch (Manufacturing)' is a sample label, not card data.
- Address: split into street, street2, city, zip, state and country (full names). Indian 6-digit PIN codes may be printed with a space, e.g. '411 057' -> '411057'.
- Never invent data. Use "" for anything not on the card.
Return ONLY a JSON object with keys: contact_name, job_position, company_name, email, phone, mobile, website, street, street2, city, zip, state, country."""

COMPANY_KEYWORDS = ('ltd', 'pvt', 'inc', 'llc', 'llp', 'studio', 'agency', 'technologies',
                     'solutions', 'company', 'corp', 'enterprises', 'industries', 'group', 'associates')
TITLE_KEYWORDS = ('manager', 'ceo', 'cto', 'cfo', 'founder', 'director', 'head', 'president',
                   'executive', 'officer', 'engineer', 'consultant', 'owner', 'partner')
PHONE_RE = re.compile(r'[+\d][\d\s\-]{6,}\d')
EMAIL_RE = re.compile(r'[\w.\-]+@[\w.\-]+\.\w+')
WEBSITE_RE = re.compile(r'(?:https?://)?(?:www\.)?[\w\-]+\.\w{2,}(?:\.\w{2,})?', re.I)
INDIAN_MOBILE_RE = re.compile(r'\+91 [6-9]\d{4} \d{5}')


class VisitingCard(models.Model):
    """Master record for a customer visiting/business card scanned by a sales user.

    Flow: draft (scanned) -> master (contact created) -> converted (CRM lead created)
    """
    _name = 'visiting.card'
    _description = 'Visiting Card'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'
    _rec_name = 'contact_name'

    name = fields.Char(string='Reference', copy=False, readonly=True, default='New')
    image_front = fields.Binary(string='Card Image (Front)', required=True, attachment=True)
    image_back = fields.Binary(string='Card Image (Back)', attachment=True)

    state = fields.Selection([
        ('draft', 'New / Scanned'),
        ('master', 'Master Created'),
        ('converted', 'Lead Created'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', tracking=True, copy=False)

    # --- Extracted / entered details ---
    contact_name = fields.Char(string='Contact Name', tracking=True)
    job_position = fields.Char(string='Designation')
    company_name = fields.Char(string='Company Name', tracking=True)
    email = fields.Char(string='Email')
    phone = fields.Char(string='Phone')
    mobile = fields.Char(string='Mobile')
    website = fields.Char(string='Website')

    street = fields.Char(string='Street')
    street2 = fields.Char(string='Street2')
    city = fields.Char(string='City')
    state_id = fields.Many2one('res.country.state', string='State')
    zip = fields.Char(string='ZIP')
    country_id = fields.Many2one('res.country', string='Country')

    raw_ocr_text = fields.Text(string='Raw Scanned Text',
                                help='Unstructured text extracted from the card image, before it is parsed into the fields above.')
    notes = fields.Text(string='Meeting Notes')

    scanned_by = fields.Many2one('res.users', string='Scanned By', default=lambda self: self.env.user, tracking=True)
    scan_date = fields.Datetime(string='Scan Date', default=fields.Datetime.now)
    meeting_date = fields.Date(string='Meeting Date', default=fields.Date.context_today)

    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    partner_id = fields.Many2one('res.partner', string='Contact Master', readonly=True, copy=False,
                                  help='Contact master created from this visiting card.')
    partner_request_id = fields.Many2one('partner.request', string='Contact Request', readonly=True, copy=False,
                                          help='Contact creation request submitted for approval from this visiting card.')
    lead_id = fields.Many2one('crm.lead', string='CRM Lead/Opportunity', readonly=True, copy=False,
                               help='CRM lead created from this visiting card master.')

    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('visiting.card') or 'New'
        return super().create(vals_list)

    def action_extract_data(self):
        """OCR the card image(s), then let the AI server place each value in the right field.

        Falls back to the local heuristic parser when the AI server is unreachable.
        """
        for card in self:
            if not card.image_front:
                raise UserError('Please attach the front image of the card before scanning.')
            text = card._ocr_image(card.image_front)
            if card.image_back:
                back_text = card._ocr_image(card.image_back)
                if back_text.strip():
                    text = f"{text}\n--- BACK SIDE ---\n{back_text}"
            if not text.strip():
                raise UserError('No text could be read from the card image. Please retake a clearer photo.')
            card.raw_ocr_text = text

            vals = card._ai_extract(text)
            if vals is None:
                card.message_post(body='AI server unreachable; used basic text parser instead. Please verify the fields.')
                card._auto_fill_from_ocr_text(text)
            else:
                card._apply_extracted(vals)
        return True

    @api.model
    def _ocr_image(self, image_b64):
        try:
            import pytesseract
            from PIL import Image, ImageOps
        except ImportError:
            raise UserError(
                'OCR libraries (pytesseract, Pillow) are not installed on this server.\n'
                'Please install them, or fill in the contact details manually below.'
            )
        from PIL import ImageFilter

        image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
        image = ImageOps.exif_transpose(image).convert('L')
        if image.width < 1600:
            ratio = 1600 / image.width
            image = image.resize((1600, int(image.height * ratio)), Image.LANCZOS)
        image = ImageOps.autocontrast(image)
        # Dark cards (light text on dark background) OCR far better inverted.
        if sum(image.resize((64, 64)).getdata()) / 4096 < 110:
            image = ImageOps.invert(image)

        def clean(t):
            return '\n'.join(line for line in t.splitlines() if line.strip())

        pass1 = clean(pytesseract.image_to_string(image, config='--psm 3'))
        sharp = image.resize((image.width * 2, image.height * 2), Image.LANCZOS).filter(
            ImageFilter.UnsharpMask(radius=4, percent=200, threshold=2))
        pass2 = clean(pytesseract.image_to_string(sharp, config='--psm 6'))
        if not pass2 or pass2 == pass1:
            return pass1
        return f"[OCR pass 1]\n{pass1}\n[OCR pass 2]\n{pass2}"

    @api.model
    def _ai_extract(self, text):
        """Ask the LLM to structure the OCR text. Returns a dict, or None if the AI call fails."""
        params = self.env['ir.config_parameter'].sudo()
        base_url = (params.get_param('visiting_card.ai_url') or '').rstrip('/')
        model = params.get_param('visiting_card.ai_model')
        if not base_url or not model:
            return None
        try:
            resp = requests.post(
                f"{base_url}/v1/chat/completions",
                headers={'ngrok-skip-browser-warning': '1'},
                json={
                    'model': model,
                    'temperature': 0,
                    'response_format': {'type': 'json_object'},
                    'messages': [
                        {'role': 'system', 'content': AI_SYSTEM_PROMPT},
                        {'role': 'user', 'content': text},
                    ],
                },
                timeout=int(params.get_param('visiting_card.ai_timeout', 60)),
            )
            resp.raise_for_status()
            content = resp.json()['choices'][0]['message']['content']
            data = json.loads(content[content.find('{'):content.rfind('}') + 1])
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            _logger.warning('Visiting card AI extraction failed: %s', exc)
            return None
        return data if isinstance(data, dict) else None

    def _apply_extracted(self, data):
        self.ensure_one()
        vals = {}
        for key in AI_FIELDS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                vals[key] = value.strip()
        for key in ('phone', 'mobile'):
            if vals.get(key):
                vals[key] = self._normalize_number(vals[key])
                if not vals[key]:
                    del vals[key]
        if vals.get('phone') and vals.get('mobile'):
            p, m = re.sub(r'\D', '', vals['phone']), re.sub(r'\D', '', vals['mobile'])
            # Two OCR passes often yield the same number with one misread digit.
            if p[-5:] == m[-5:] or difflib.SequenceMatcher(None, p[-10:], m[-10:]).ratio() >= 0.8:
                del vals['phone']
        # Indian mobile numbers belong in Mobile even when the model labels them as phone.
        if vals.get('phone') and not vals.get('mobile') and INDIAN_MOBILE_RE.fullmatch(vals['phone']):
            vals['mobile'] = vals.pop('phone')
        if vals.get('website'):
            site = vals['website'].replace(' ', '').lower()
            if WEBSITE_RE.fullmatch(site):
                vals['website'] = site
            else:
                del vals['website']
        if vals.get('email'):
            vals['email'] = self._correct_email(
                vals['email'].replace(' ', '').lower(),
                vals.get('contact_name', ''), vals.get('website', ''), vals.get('company_name', ''))
            if not EMAIL_RE.fullmatch(vals['email']):
                del vals['email']

        country = False
        if data.get('country'):
            country = self.env['res.country'].search(
                ['|', ('name', '=ilike', data['country'].strip()), ('code', '=ilike', data['country'].strip())], limit=1)
        if not country and (vals.get('mobile', '') + vals.get('phone', '')).replace(' ', '').startswith('+91'):
            country = self.env.ref('base.in', raise_if_not_found=False)
        if country:
            vals['country_id'] = country.id
        if data.get('state'):
            domain = [('name', '=ilike', data['state'].strip())]
            if country:
                domain.append(('country_id', '=', country.id))
            state = self.env['res.country.state'].search(domain, limit=1)
            if state:
                vals['state_id'] = state.id
                vals.setdefault('country_id', state.country_id.id)
        self.write(vals)

    @staticmethod
    def _correct_email(email, contact_name, website, company_name):
        """Repair 1-2 misread letters by comparing against the printed name, website and company."""
        if '@' not in email:
            return email
        local, domain = email.split('@', 1)

        name_parts = [re.sub(r'[^a-z]', '', p) for p in contact_name.lower().split()]
        name_parts = [p for p in name_parts if p]
        if name_parts:
            sep = next((c for c in '._-' if c in local), '')
            candidates = {sep.join(name_parts), ''.join(name_parts), f"{name_parts[0]}{sep}{name_parts[-1][:1]}"}
            best = max(candidates, key=lambda c: difflib.SequenceMatcher(None, local, c).ratio())
            if local != best and difflib.SequenceMatcher(None, local, best).ratio() >= 0.8:
                local = best

        if '.' in domain:
            name, tld = domain.rsplit('.', 1)
            refs = []
            if website:
                refs.append(re.sub(r'^(https?://)?(www\.)?', '', website).rsplit('.', 1)[0])
            words = re.sub(r'[^a-z ]', '', company_name.lower()).split()
            if words:
                refs += [words[0], ''.join(words[:2])]
            best = max(refs, key=lambda r: difflib.SequenceMatcher(None, name, r).ratio(), default='')
            if best and name != best and difflib.SequenceMatcher(None, name, best).ratio() >= 0.8:
                domain = f"{best}.{tld}"
        return f"{local}@{domain}"

    @staticmethod
    def _normalize_number(number):
        """Format as '+CC XXXXX XXXXX' for Indian numbers; return '' if it isn't a plausible number."""
        digits = re.sub(r'\D', '', number)
        if len(digits) == 12 and digits.startswith('91'):
            digits = digits[2:]
        if len(digits) == 11 and digits.startswith('0') and digits[1] in '6789':
            digits = digits[1:]
        if len(digits) == 10 and digits[0] in '6789':
            return f"+91 {digits[:5]} {digits[5:]}"
        if 8 <= len(digits) <= 15:
            return number.strip()
        return ''

    @staticmethod
    def _parse_ocr_text(text):
        """Best-effort heuristic parser: turns raw OCR text into structured fields.

        Not a substitute for a real NLP/vision parser, but good enough to save the
        rep from retyping the obvious fields (name, company, email, phone, website)
        for common card layouts. Address fields are left for manual entry since
        free-text addresses are too varied to parse reliably this way.
        """
        vals = {}
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            low = line.lower()
            if '@' in line and 'email' not in vals:
                m = EMAIL_RE.search(line)
                if m:
                    vals['email'] = m.group(0)
                continue
            if ('www.' in low or low.startswith(('w:', 'w.', 'web', 'website'))) and 'website' not in vals:
                m = WEBSITE_RE.search(line)
                if m:
                    vals['website'] = m.group(0)
                continue
            if re.search(r'\b(mobile|mob|m)\s*[:.]', low) and 'mobile' not in vals:
                m = PHONE_RE.search(line)
                if m:
                    vals['mobile'] = m.group(0).strip()
                continue
            if re.search(r'\b(phone|ph|tel)\s*[:.]', low) and 'phone' not in vals:
                m = PHONE_RE.search(line)
                if m:
                    vals['phone'] = m.group(0).strip()
                continue
            if any(k in low for k in TITLE_KEYWORDS) and 'job_position' not in vals:
                vals['job_position'] = line
                continue
            if any(k in low for k in COMPANY_KEYWORDS) and 'company_name' not in vals:
                vals['company_name'] = line
                continue
            if not re.search(r'\d', line) and 'contact_name' not in vals and len(line.split()) <= 5:
                vals['contact_name'] = line
        if 'phone' not in vals and 'mobile' not in vals:
            m = PHONE_RE.search(text)
            if m:
                vals['phone'] = m.group(0).strip()
        return vals

    def _auto_fill_from_ocr_text(self, text):
        """Fill in blank fields from parsed OCR text, never overwriting what's already there."""
        self.ensure_one()
        parsed = self._parse_ocr_text(text)
        update_vals = {k: v for k, v in parsed.items() if not self[k]}
        if update_vals:
            self.write(update_vals)

    def action_create_master(self):
        """Submit a Contact Creation Request for this card, using the company's standard
        partner_creation approval flow, instead of creating the res.partner directly.
        """
        self.ensure_one()
        if self.partner_request_id:
            return self._open_partner_request()
        if not self.contact_name and not self.company_name:
            raise UserError('Please fill in at least the Contact Name or Company Name before requesting a contact.')

        request = self.env['partner.request'].create({
            'name': self.company_name or self.contact_name,
            'opportunity_id': self.lead_id.id,
            'street': self.street,
            'street2': self.street2,
            'city': self.city,
            'state_id': self.state_id.id,
            'zip': self.zip,
            'country': self.country_id.id,
        })
        self.write({'partner_request_id': request.id, 'state': 'master'})
        return self._open_partner_request()

    def _open_partner_request(self):
        self.ensure_one()
        form_view = self.env.ref('partner_creation.partner_request_view_form')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'partner.request',
            'view_mode': 'form',
            'views': [[form_view.id, 'form']],
            'res_id': self.partner_request_id.id,
            'target': 'current',
        }

    def action_create_lead(self):
        """Convert the visiting card master into a CRM lead/opportunity."""
        for card in self:
            if not card.partner_id:
                card.action_create_master()
            if card.lead_id:
                continue
            description_parts = [p for p in [card.notes, card.raw_ocr_text] if p]
            lead_vals = {
                'name': f"{card.contact_name or card.company_name} - Visiting Card",
                'partner_id': card.partner_id.id,
                'contact_name': card.contact_name,
                'partner_name': card.company_name,
                'function': card.job_position,
                'email_from': card.email,
                'phone': card.phone or card.mobile,
                'website': card.website,
                'street': card.street,
                'street2': card.street2,
                'city': card.city,
                'state_id': card.state_id.id,
                'zip': card.zip,
                'country_id': card.country_id.id,
                'description': '\n\n'.join(description_parts),
                'user_id': card.scanned_by.id,
                'visiting_card_id': card.id,
            }
            if 'mobile' in self.env['crm.lead']._fields:
                lead_vals['mobile'] = card.mobile
            lead = self.env['crm.lead'].create(lead_vals)
            card.lead_id = lead
            card.state = 'converted'
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'view_mode': 'form',
            'res_id': self.lead_id.id,
            'target': 'current',
        }

    def action_view_partner(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'view_mode': 'form',
            'res_id': self.partner_id.id,
            'target': 'current',
        }

    def action_view_partner_request(self):
        self.ensure_one()
        return self._open_partner_request()

    def action_view_lead(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'view_mode': 'form',
            'res_id': self.lead_id.id,
            'target': 'current',
        }

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})
