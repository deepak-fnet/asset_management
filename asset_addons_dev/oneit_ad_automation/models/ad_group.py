# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

GROUP_TYPES = [
    ('ad_folder', 'AD Folder'),
    ('citrix', 'Citrix'),
    ('file_server', 'File Server'),
]


class OneitAdGroup(models.Model):
    """An Active Directory container.

    ``ad_folder`` is the OU the user object itself is created in / moved to;
    ``citrix`` and ``file_server`` are security groups the user is added to.
    """
    _name = 'oneit.ad.group'
    _description = 'AD Group'
    _order = 'group_type, name'

    name = fields.Char(string='Name', required=True)
    group_type = fields.Selection(
        GROUP_TYPES, string='Type', required=True, default='citrix',
        help="AD Folder is the OU the user object lives in. Citrix and "
             "File Server groups are memberships granted to the user.")
    distinguished_name = fields.Char(
        string='Distinguished Name', required=True,
        help="Full DN, e.g. OU=Finance,OU=Users,DC=example,DC=local")
    description = fields.Char(string='Description')
    team_ids = fields.Many2many(
        'oneit.team', 'oneit_team_ad_group_rel', 'ad_group_id', 'team_id',
        string='Teams')
    active = fields.Boolean(default=True)

    synced_from_ad = fields.Boolean(
        string='Synced from AD', default=False, readonly=True,
        help="Discovered by the sync rather than entered by hand. Name and "
             "DN are managed by Active Directory and should not be edited "
             "here - the next sync would overwrite them.")
    last_synced_on = fields.Datetime(string='Last Seen in AD', readonly=True)

    membership_ids = fields.One2many(
        'oneit.ad.membership', 'group_id', string='Members in AD',
        help="Who is actually in this group in Active Directory, as of the "
             "last sync. Read-only mirror - editing here does not change AD.")
    member_count = fields.Integer(
        string='Members', compute='_compute_member_count', store=True)

    # Uniqueness is on the DN, not the name: AD permits two groups with the
    # same CN in different OUs, and every LDAP operation addresses the DN.
    _sql_constraints = [
        ('dn_uniq', 'unique(distinguished_name)',
         'An AD group with this distinguished name already exists.'),
    ]

    @api.constrains('distinguished_name')
    def _check_distinguished_name(self):
        for record in self:
            dn = (record.distinguished_name or '').strip()
            if dn and '=' not in dn:
                raise ValidationError(_(
                    "'%s' does not look like a valid distinguished name. "
                    "Expected something like OU=Finance,DC=example,DC=local"
                ) % dn)

    @api.depends('membership_ids')
    def _compute_member_count(self):
        for rec in self:
            rec.member_count = len(rec.membership_ids)

    @api.depends('name', 'group_type')
    def _compute_display_name(self):
        # Odoo 17 removed name_get(); display_name is now a computed field
        # that each model overrides directly.
        type_labels = dict(GROUP_TYPES)
        for rec in self:
            rec.display_name = '%s (%s)' % (
                rec.name, type_labels.get(rec.group_type, ''))

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    @api.model
    def _classify_group(self, name, dn):
        """Decide citrix vs file_server for a discovered group.

        Active Directory has no field meaning "this is a Citrix group", so
        this has to be inferred. In the newgenkw.local directory the
        CONTAINER is authoritative and the name is not: groups are filed
        into purpose-specific OUs, and this rule reproduces the
        hand-maintained classification of all 349 groups exactly.

            OU=IMAGE GROUPS,OU=NGKW GROUPS   -> citrix       (125 groups)
            any other OU under NGKW GROUPS   -> file_server  (224 groups)

        Name matching was tried first and is not viable here: B-QC-USERS
        is Citrix and EDU LOGIN USERS is File Server, with no token to
        separate them, and the EDUCATION_DIGITAL groups are File Server
        despite containing no "FS" anywhere in the name.

        Returns (group_type, confident). confident=False means the group
        sat outside every known container and needs a human to look at it.
        """
        haystack = (dn or '').upper()

        # --- AD Folder: containers that hold user accounts -------------
        # Under OU=U4-DTLP Users the leaf OUs (ACCOUNTS, ADMIN, CBPT,
        # CJPT, Data_Division ...) are where user objects live, so they
        # are the OU a new joiner is created in - not security groups.
        #
        # ad_detail_3.csv labels all 25 of these "File Server", which is
        # wrong: they are containers, not shares. The Django UI defaulted
        # the field and nobody corrected it. Classifying them by DN fixes
        # that inherited mistake rather than importing it.
        if 'OU=U4-DTLP USERS' in haystack:
            return 'ad_folder', True

        # --- Citrix: the single image-groups container ------------------
        if 'OU=IMAGE GROUPS' in haystack:
            return 'citrix', True

        # --- File server: the known share containers --------------------
        # Listed explicitly rather than treating "everything else" as
        # file_server, so a group in a NEW container gets flagged for
        # review instead of silently assumed.
        file_server_ous = (
            'OU=FS GROUPS',
            'OU=JOURNALS FS',
            'OU=BOOKS FS',
            'OU=PLOS FS',
            'OU=NPUK FS',
            'OU=EDUCATION_FS',
            'OU=EDUCATION_DIGITAL',
            'OU=ELS_FS',
        )
        for container in file_server_ous:
            if container in haystack:
                return 'file_server', True

        # --- Fallback ---------------------------------------------------
        # Anything outside the containers above. Generic name tokens as a
        # last resort, then default to citrix and flag for review - never
        # skip, or the group would vanish from the UI entirely.
        name_haystack = ('%s %s' % (name or '', dn or '')).upper()
        if any(token in name_haystack for token in
               ('-FS0', '-FS-', 'FILESERVER', 'FILE-SERVER', 'SHARE', 'NTFS')):
            return 'file_server', False
        if any(token in name_haystack for token in
               ('CITRIX', '-CTX-', 'XENAPP', 'PUBLISHED')):
            return 'citrix', False
        return 'citrix', False

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    @api.model
    def sync_from_ad(self, archive_missing=True):
        """Pull the group and OU list from AD into oneit.ad.group.

        create : in AD, not in Odoo       -> new record
        update : in both                  -> refresh name / description
        archive: in Odoo, gone from AD    -> active = False

        Records are ARCHIVED, never deleted. A group may be referenced by
        historical requests; unlinking would either fail on the foreign key
        or silently rewrite the audit trail. Archived records stop
        appearing in selection lists, which is the intended effect.

        Manually created records (synced_from_ad = False) are never
        archived - IT may have added one ahead of it existing in AD.
        """
        connector = self.env['oneit.ad.connector']

        try:
            ad_groups = connector.search_ad_groups()
            ad_ous = connector.search_ad_ous()
        except UserError:
            raise
        except Exception as exc:
            _logger.exception("AD group sync failed")
            raise UserError(_(
                "Could not read the directory:\n\n%s\n\n"
                "Check the connection settings under Settings > "
                "OneIT AD Automation."
            ) % exc)

        now = fields.Datetime.now()
        stats = {'created': 0, 'updated': 0, 'archived': 0,
                 'reactivated': 0, 'unclassified': 0}

        # OUs first, so a name collision between an OU and a group resolves
        # in favour of the OU.
        incoming = {}
        for ou in ad_ous:
            incoming[ou['dn']] = {
                'name': ou['name'],
                'distinguished_name': ou['dn'],
                'description': ou['description'],
                'group_type': 'ad_folder',
            }
        for grp in ad_groups:
            if grp['dn'] in incoming:
                continue
            group_type, confident = self._classify_group(grp['name'], grp['dn'])
            if not confident:
                stats['unclassified'] += 1
            incoming[grp['dn']] = {
                'name': grp['name'],
                'distinguished_name': grp['dn'],
                'description': grp['description'],
                'group_type': group_type,
            }

        # Include archived records, or a group deleted then recreated in AD
        # would hit the unique DN constraint instead of being reactivated.
        existing = self.with_context(active_test=False).search([])
        by_dn = {rec.distinguished_name: rec for rec in existing}

        for dn, values in incoming.items():
            record = by_dn.get(dn)
            if record:
                changes = {}
                if record.name != values['name']:
                    changes['name'] = values['name']
                if values['description'] and \
                        record.description != values['description']:
                    changes['description'] = values['description']
                if not record.active:
                    changes['active'] = True
                    stats['reactivated'] += 1

                # group_type is deliberately NOT overwritten. The classifier
                # is a guess; a correction made in the UI must survive every
                # future sync.

                if changes:
                    stats['updated'] += 1
                changes['last_synced_on'] = now
                changes['synced_from_ad'] = True
                record.write(changes)
            else:
                values.update({
                    'synced_from_ad': True,
                    'last_synced_on': now,
                    'active': True,
                })
                self.create(values)
                stats['created'] += 1

        if archive_missing:
            stale = existing.filtered(
                lambda r: r.active
                and r.synced_from_ad
                and r.distinguished_name not in incoming
            )
            if stale:
                stats['archived'] = len(stale)
                _logger.info(
                    "Archiving %s AD group(s) no longer in the directory: %s",
                    len(stale), ', '.join(stale.mapped('name')))
                stale.write({'active': False})

        _logger.info("AD group sync: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Membership sync (who is actually in each group)
    # ------------------------------------------------------------------
    @api.model
    def sync_memberships_from_ad(self):
        """Mirror AD account list and group membership into Odoo.

        Runs after sync_from_ad, because memberships attach to group
        records that must already exist.

        Accounts and memberships that disappear from AD are UNLINKED, not
        archived - unlike groups. A membership is a statement of present
        fact, so a stale one is actively misleading; and the request
        history, which is the audit trail, lives on oneit.request and is
        untouched by this.
        """
        connector = self.env['oneit.ad.connector']
        Person = self.env['oneit.ad.person']
        Membership = self.env['oneit.ad.membership']

        try:
            ad_users = connector.search_ad_users()
            ad_groups = connector.search_ad_groups(with_members=True)
        except UserError:
            raise
        except Exception as exc:
            _logger.exception("AD membership sync failed")
            raise UserError(_(
                "Could not read directory membership:\n\n%s") % exc)

        now = fields.Datetime.now()
        stats = {'people_created': 0, 'people_updated': 0,
                 'people_removed': 0, 'memberships_created': 0,
                 'memberships_removed': 0, 'unmanaged_people': 0,
                 'truncated_groups': []}

        # --- accounts ---------------------------------------------------
        existing_people = Person.with_context(active_test=False).search([])
        people_by_dn = {p.distinguished_name.lower(): p
                        for p in existing_people if p.distinguished_name}
        seen_person_ids = set()

        for user in ad_users:
            dn_key = (user['dn'] or '').lower()
            if not dn_key or not user.get('username'):
                continue
            values = {
                'username': user['username'],
                'name': user.get('name') or '',
                'email': user.get('email') or '',
                'distinguished_name': user['dn'],
                'disabled_in_ad': user.get('disabled', False),
                'last_synced_on': now,
                'active': True,
            }
            person = people_by_dn.get(dn_key)
            if person:
                person.write(values)
                stats['people_updated'] += 1
            else:
                values['first_seen_on'] = now
                person = Person.create(values)
                people_by_dn[dn_key] = person
                stats['people_created'] += 1
            seen_person_ids.add(person.id)

        # Link each account to its AD Folder record, when the OU is known.
        folders = {g.distinguished_name.lower(): g.id
                   for g in self.with_context(active_test=False).search(
                       [('group_type', '=', 'ad_folder')])
                   if g.distinguished_name}
        for person in Person.browse(list(seen_person_ids)):
            person.ou_group_id = folders.get((person.ou_dn or '').lower())

        # --- memberships ------------------------------------------------
        groups_by_dn = {g.distinguished_name.lower(): g
                        for g in self.with_context(active_test=False).search([])
                        if g.distinguished_name}

        existing_memberships = Membership.search([])
        mem_by_key = {(m.person_id.id, m.group_id.id): m
                      for m in existing_memberships}
        seen_mem_keys = set()

        for ad_group in ad_groups:
            group = groups_by_dn.get((ad_group['dn'] or '').lower())
            if not group:
                continue
            if ad_group.get('members_truncated'):
                # Recorded and surfaced rather than swallowed: a partially
                # read group looks identical to a small group otherwise.
                stats['truncated_groups'].append(group.name)

            for member_dn in ad_group.get('members') or []:
                person = people_by_dn.get((member_dn or '').lower())
                if not person:
                    # A member that is not a user object - a nested group,
                    # a computer, or a contact. Out of scope here.
                    continue
                key = (person.id, group.id)
                seen_mem_keys.add(key)
                membership = mem_by_key.get(key)
                if membership:
                    membership.write({'last_synced_on': now,
                                      'member_dn': member_dn})
                else:
                    Membership.create({
                        'group_id': group.id,
                        'person_id': person.id,
                        'member_dn': member_dn,
                        'first_seen_on': now,
                        'last_synced_on': now,
                    })
                    stats['memberships_created'] += 1

        stale_mem = existing_memberships.filtered(
            lambda m: (m.person_id.id, m.group_id.id) not in seen_mem_keys)
        if stale_mem:
            stats['memberships_removed'] = len(stale_mem)
            stale_mem.unlink()

        stale_people = existing_people.filtered(
            lambda p: p.id not in seen_person_ids)
        if stale_people:
            stats['people_removed'] = len(stale_people)
            stale_people.write({'active': False})

        stats['unmanaged_people'] = Person.search_count([
            ('known_to_odoo', '=', False)])

        _logger.info("AD membership sync: %s", stats)
        return stats

    @api.model
    def cron_sync_ad_groups(self):
        """Scheduled entry point. Never raises: Odoo disables a cron that
        throws repeatedly, which would stop the sync silently."""
        try:
            self.sync_from_ad()
            self.sync_memberships_from_ad()
        except Exception:
            _logger.exception("Scheduled AD sync failed")
        return True

    def action_sync_from_ad(self):
        """Button / action menu entry on the AD Groups list."""
        stats = self.sync_from_ad()
        mem = self.sync_memberships_from_ad()

        message = _(
            "Groups: %(created)s created, %(updated)s updated, "
            "%(archived)s archived, %(reactivated)s reactivated."
        ) % stats
        message += _(
            "\nAccounts: %(people_created)s new, %(people_updated)s updated."
            "\nMemberships: %(memberships_created)s added, "
            "%(memberships_removed)s removed."
        ) % mem

        if mem['unmanaged_people']:
            message += _(
                "\n\n%s AD account(s) exist that OneIT has no request for. "
                "See Directory > AD Accounts, filter 'Not managed by "
                "OneIT'."
            ) % mem['unmanaged_people']
        if stats['unclassified']:
            message += _(
                "\n\n%s group(s) fell outside the known containers and were "
                "defaulted to Citrix. Check them."
            ) % stats['unclassified']
        if mem['truncated_groups']:
            message += _(
                "\n\nWARNING - membership could not be read completely for: "
                "%s. Those groups show fewer members than they have."
            ) % ', '.join(mem['truncated_groups'])
        if self.env['oneit.ad.connector']._simulate():
            message += _(
                "\n\nSIMULATION MODE - this is sample data, not your real "
                "directory.")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("AD Sync Complete"),
                'message': message,
                'type': 'success',
                'sticky': True,
            },
        }
