# -*- coding: utf-8 -*-
"""Active Directory connector.

Port of the Django project's ``AutoITHelper`` plus the LDAP helpers that lived
inside the ``ad_disable`` / ``ad_remove_user`` / ``ad_update`` management
commands. All AD writes in this module funnel through here.

Set the system parameter ``oneit_ad.simulate`` to ``True`` to exercise the
approval workflow without touching a domain controller: every operation is
logged and reported as successful instead of being sent over LDAP.
"""

import logging
import ssl

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from ldap3 import (
        ALL,
        MODIFY_ADD,
        MODIFY_DELETE,
        MODIFY_REPLACE,
        SUBTREE,
        Connection,
        Server,
        Tls,
    )
except ImportError:  # pragma: no cover
    _logger.warning("python library 'ldap3' not found; AD operations disabled")
    Connection = Server = Tls = None
    ALL = MODIFY_ADD = MODIFY_DELETE = MODIFY_REPLACE = SUBTREE = None

# userAccountControl flags
UAC_NORMAL_ACCOUNT = 512
UAC_ACCOUNTDISABLE = 0x2


class OneitAdConnector(models.AbstractModel):
    _name = 'oneit.ad.connector'
    _description = 'Active Directory Connector'

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @api.model
    def _get_param(self, key, default=''):
        return self.env['ir.config_parameter'].sudo().get_param(key, default)

    @api.model
    def _simulate(self):
        """Dry-run mode: log instead of writing to AD."""
        value = self._get_param('oneit_ad.simulate', 'True')
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on')

    @api.model
    def _get_settings(self):
        return {
            'server_uri': self._get_param('oneit_ad.server_uri'),
            'bind_dn': self._get_param('oneit_ad.bind_dn'),
            'bind_password': self._get_param('oneit_ad.bind_password'),
            'base_dn': self._get_param('oneit_ad.base_dn'),
            'domain': self._get_param('oneit_ad.domain'),
            'use_ssl': str(
                self._get_param('oneit_ad.use_ssl', 'True')
            ).strip().lower() in ('1', 'true', 'yes', 'on'),
        }

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    @api.model
    def _connect(self):
        """Return a bound ldap3 Connection, or raise UserError."""
        if Connection is None:
            raise UserError(_(
                "The Python library 'ldap3' is not installed on the Odoo "
                "server. Install it with: pip3 install ldap3"
            ))
        cfg = self._get_settings()
        missing = [k for k in ('server_uri', 'bind_dn', 'bind_password', 'base_dn')
                   if not cfg.get(k)]
        if missing:
            raise UserError(_(
                "Active Directory is not configured. Missing: %s\n\n"
                "Set it under Settings > OneIT AD Automation."
            ) % ', '.join(missing))
        try:
            tls = Tls(validate=ssl.CERT_NONE)
            server = Server(
                cfg['server_uri'], get_info=ALL,
                use_ssl=cfg['use_ssl'], tls=tls,
            )
            return Connection(
                server, user=cfg['bind_dn'],
                password=cfg['bind_password'], auto_bind=True,
            )
        except Exception as exc:
            raise UserError(_("Could not connect to Active Directory:\n%s") % exc)

    @api.model
    def test_connection(self):
        """Called from the settings screen."""
        if self._simulate():
            raise UserError(_(
                "Simulation mode is ON. Requests will complete without "
                "contacting Active Directory.\n\n"
                "Turn simulation off to test a real connection."
            ))
        conn = self._connect()
        conn.unbind()
        raise UserError(_("Connection to Active Directory succeeded."))

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------
    @api.model
    def find_user_dn(self, sam_account_name):
        """Return the distinguishedName for a sAMAccountName, or False."""
        if self._simulate():
            base_dn = self._get_param('oneit_ad.base_dn', 'DC=example,DC=local')
            return 'CN=%s,%s' % (sam_account_name, base_dn)
        conn = self._connect()
        try:
            conn.search(
                search_base=self._get_settings()['base_dn'],
                search_filter='(sAMAccountName=%s)' % sam_account_name,
                search_scope=SUBTREE,
                attributes=['distinguishedName', 'cn', 'memberOf',
                            'userAccountControl'],
            )
            if not conn.entries:
                return False
            return conn.entries[0].distinguishedName.value
        finally:
            conn.unbind()

    @api.model
    def get_user_groups(self, sam_account_name):
        """Return the list of group DNs the user belongs to."""
        if self._simulate():
            return []
        conn = self._connect()
        try:
            conn.search(
                search_base=self._get_settings()['base_dn'],
                search_filter='(sAMAccountName=%s)' % sam_account_name,
                search_scope=SUBTREE,
                attributes=['memberOf', 'distinguishedName'],
            )
            if not conn.entries:
                return []
            entry = conn.entries[0]
            return list(entry.memberOf.values) if 'memberOf' in entry else []
        finally:
            conn.unbind()

    # ------------------------------------------------------------------
    # Directory enumeration (read-only)
    # ------------------------------------------------------------------
    @api.model
    def search_ad_groups(self, with_members=False):
        """Return every security group under the base DN.

        Each item: {'name', 'dn', 'description'} plus, when with_members
        is set, {'members': [dn, ...], 'members_truncated': bool}.

        Membership lives on the group as the multi-valued ``member``
        attribute, so it costs one extra attribute on a search this method
        already runs - not a second round trip per group.
        """
        if self._simulate():
            return self._simulated_groups(with_members=with_members)

        conn = self._connect()
        try:
            results = []
            attributes = ['cn', 'distinguishedName', 'description']
            if with_members:
                attributes.append('member')

            # paged_search handles AD's 1000-object return limit, which a
            # plain search() would silently truncate.
            entries = conn.extend.standard.paged_search(
                search_base=self._get_settings()['base_dn'],
                search_filter='(objectClass=group)',
                search_scope=SUBTREE,
                attributes=attributes,
                paged_size=500,
                generator=True,
            )
            for entry in entries:
                if entry.get('type') != 'searchResEntry':
                    continue
                attrs = entry.get('attributes', {})
                dn = attrs.get('distinguishedName')
                cn = attrs.get('cn')
                if not dn or not cn:
                    continue
                item = {
                    'name': self._first(cn),
                    'dn': self._first(dn),
                    'description': self._first(attrs.get('description')),
                }
                if with_members:
                    members = attrs.get('member') or []
                    if isinstance(members, str):
                        members = [members]
                    item['members'] = list(members)
                    item['members_truncated'] = False

                    # AD caps a single attribute read at MaxValRange
                    # (1500 by default) and returns the first slice with
                    # NO error. Anything at or above the cap has to be
                    # re-read with explicit range retrieval or the group
                    # would silently appear to have fewer members than it
                    # does - the worst kind of wrong, because it looks
                    # like a successful sync.
                    if len(item['members']) >= 1500:
                        full = self._fetch_all_members(conn, item['dn'])
                        if full is None:
                            item['members_truncated'] = True
                        else:
                            item['members'] = full
                results.append(item)
            return results
        finally:
            conn.unbind()

    def _fetch_all_members(self, conn, group_dn):
        """Page through ``member`` using AD range retrieval.

        Returns the complete list, or None if paging failed (caller then
        flags the group as truncated rather than reporting a short list as
        if it were complete).
        """
        collected = []
        step = 1500
        start = 0
        try:
            while True:
                attr = 'member;range=%d-%d' % (start, start + step - 1)
                ok = conn.search(
                    search_base=group_dn,
                    search_filter='(objectClass=group)',
                    search_scope='BASE',
                    attributes=[attr],
                )
                if not ok or not conn.entries:
                    break

                raw = conn.entries[0].entry_attributes_as_dict
                # AD renames the key to reflect the range it actually
                # returned, and marks the final slice with '*', e.g.
                # 'member;range=1500-*'. So find the member key rather
                # than assuming the one we asked for came back.
                chunk, final = [], False
                for key, value in raw.items():
                    if not key.lower().startswith('member'):
                        continue
                    chunk = value if isinstance(value, list) else [value]
                    final = key.endswith('-*')
                    break

                if not chunk:
                    break
                collected.extend(chunk)
                if final or len(chunk) < step:
                    break
                start += len(chunk)

                if start > 100000:  # runaway guard
                    _logger.warning(
                        "Stopping member paging for %s at %s entries",
                        group_dn, start)
                    return None
            return collected
        except Exception:
            _logger.exception("Range retrieval failed for %s", group_dn)
            return None

    @api.model
    def search_ad_ous(self):
        """Return every organizational unit under the base DN.

        These become group_type='ad_folder' records - the OUs a user
        object can be created in or moved to.
        """
        if self._simulate():
            return self._simulated_ous()

        conn = self._connect()
        try:
            results = []
            entries = conn.extend.standard.paged_search(
                search_base=self._get_settings()['base_dn'],
                search_filter='(objectClass=organizationalUnit)',
                search_scope=SUBTREE,
                attributes=['ou', 'distinguishedName', 'description'],
                paged_size=500,
                generator=True,
            )
            for entry in entries:
                if entry.get('type') != 'searchResEntry':
                    continue
                attrs = entry.get('attributes', {})
                dn = attrs.get('distinguishedName')
                ou = attrs.get('ou')
                if not dn or not ou:
                    continue
                results.append({
                    'name': self._first(ou),
                    'dn': self._first(dn),
                    'description': self._first(attrs.get('description')),
                })
            return results
        finally:
            conn.unbind()

    @api.model
    def _first(self, value):
        """ldap3 returns some attributes as lists, some as scalars."""
        if isinstance(value, (list, tuple)):
            return value[0] if value else ''
        return value or ''

    # ------------------------------------------------------------------
    # Simulation fixtures
    #
    # Returned when oneit_ad.simulate is True, so the sync can be
    # exercised - including create / update / archive - with no domain
    # controller present.
    #
    # SLS-Citrix-CRM is deliberately absent: it exists in the demo data,
    # so the first simulated sync archives it and you can see what a
    # removed-in-AD group looks like.
    # ------------------------------------------------------------------
    @api.model
    def search_ad_users(self):
        """Every user account under the base DN, with its OU.

        Needed because group membership alone would miss a person who is
        in no synced group at all - they would still exist in an OU and
        still be unknown to Odoo.
        """
        if self._simulate():
            return self._simulated_users()

        conn = self._connect()
        try:
            results = []
            entries = conn.extend.standard.paged_search(
                search_base=self._get_settings()['base_dn'],
                search_filter='(&(objectClass=user)(objectCategory=person))',
                search_scope=SUBTREE,
                attributes=['sAMAccountName', 'distinguishedName', 'cn',
                            'mail', 'displayName', 'userAccountControl'],
                paged_size=500,
                generator=True,
            )
            for entry in entries:
                if entry.get('type') != 'searchResEntry':
                    continue
                attrs = entry.get('attributes', {})
                dn = self._first(attrs.get('distinguishedName'))
                if not dn:
                    continue
                # Bit 2 of userAccountControl is ACCOUNTDISABLE.
                try:
                    uac = int(self._first(attrs.get('userAccountControl')) or 0)
                except (TypeError, ValueError):
                    uac = 0
                results.append({
                    'dn': dn,
                    'username': self._first(attrs.get('sAMAccountName')),
                    'name': (self._first(attrs.get('displayName'))
                             or self._first(attrs.get('cn'))),
                    'email': self._first(attrs.get('mail')),
                    'disabled': bool(uac & 2),
                })
            return results
        finally:
            conn.unbind()

    @api.model
    def _simulated_groups(self, with_members=False):
        base = self._get_param('oneit_ad.base_dn', 'DC=example,DC=local')
        names = [
            ('FIN-Citrix-ERP', 'Published ERP application'),
            ('FIN-Citrix-BI', 'Reporting and BI dashboards'),
            ('FIN-FS-Accounts', 'Accounts share'),
            ('FIN-FS-Payroll', 'Payroll share (restricted)'),
            ('ENG-Citrix-DevTools', 'IDE and build tooling'),
            ('ENG-FS-Source', 'Source share'),
            ('ENG-FS-Builds', 'Build artefact share'),
            ('SUP-Citrix-Helpdesk', 'Ticketing console'),
            ('SUP-FS-KnowledgeBase', 'Knowledge base share'),
            ('HRO-Citrix-HRMS', 'HRMS application'),
            ('HRO-FS-Records', 'HR records share (restricted)'),
            ('SLS-FS-Proposals', 'Proposals share'),
            # Not in demo data - the sync will CREATE these two.
            ('ENG-Citrix-Jenkins', 'Build server console'),
            ('FIN-FS-Audit', 'External audit share'),
        ]
        # Simulated membership. vicky.r is deliberately present in AD but
        # has no onboarding request in the demo data, so the "in AD, not
        # in Odoo" detection has something real to find without needing a
        # live directory.
        members_by_group = {
            'ENG-Citrix-DevTools': ['deepak.k', 'sasi.m', 'vicky.r'],
            'ENG-FS-Source': ['deepak.k', 'sasi.m', 'vicky.r'],
            'ENG-FS-Builds': ['deepak.k', 'vicky.r'],
            'FIN-Citrix-ERP': ['priya.b'],
            'FIN-FS-Accounts': ['priya.b', 'anjali.t'],
            'HRO-FS-Records': ['aarti.s'],
        }
        ou_of = {
            'deepak.k': 'Engineering', 'sasi.m': 'Engineering',
            'vicky.r': 'Engineering', 'priya.b': 'Finance',
            'anjali.t': 'Finance', 'aarti.s': 'HROps',
        }
        out = []
        for n, d in names:
            item = {'name': n, 'dn': 'CN=%s,OU=Groups,%s' % (n, base),
                    'description': d}
            if with_members:
                item['members'] = [
                    'CN=%s,OU=%s,OU=Users,%s' % (u, ou_of.get(u, 'Users'), base)
                    for u in members_by_group.get(n, [])
                ]
                item['members_truncated'] = False
            out.append(item)
        return out

    @api.model
    def _simulated_users(self):
        base = self._get_param('oneit_ad.base_dn', 'DC=example,DC=local')
        people = [
            ('deepak.k', 'Deepak Kumar', 'Engineering'),
            ('sasi.m', 'Sasi Mohan', 'Engineering'),
            ('vicky.r', 'Vicky Raman', 'Engineering'),
            ('priya.b', 'Priya Balan', 'Finance'),
            ('anjali.t', 'Anjali Thomas', 'Finance'),
            ('aarti.s', 'Aarti Sharma', 'HROps'),
        ]
        return [{
            'dn': 'CN=%s,OU=%s,OU=Users,%s' % (u, ou, base),
            'username': u,
            'name': name,
            'email': '%s@example.local' % u,
            'disabled': False,
        } for u, name, ou in people]

    @api.model
    def _simulated_ous(self):
        base = self._get_param('oneit_ad.base_dn', 'DC=example,DC=local')
        names = ['Finance', 'Engineering', 'Sales', 'Support', 'HROps']
        return [{'name': n, 'dn': 'OU=%s,OU=Users,%s' % (n, base),
                 'description': ''} for n in names]

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------
    @api.model
    def create_user(self, sam_account_name, display_name, email, user_ou,
                    password=None):
        """Create an AD user object inside ``user_ou`` and enable it."""
        password = password or self._get_param(
            'oneit_ad.default_password', 'Welcome@1234')
        user_dn = 'CN=%s,%s' % (display_name, user_ou)
        if self._simulate():
            _logger.info("[SIMULATE] create AD user %s (%s)",
                         user_dn, sam_account_name)
            return user_dn

        cfg = self._get_settings()
        conn = self._connect()
        try:
            attributes = {
                'objectClass': ['top', 'person', 'organizationalPerson', 'user'],
                'sAMAccountName': sam_account_name,
                'userPrincipalName': '%s@%s' % (sam_account_name, cfg['domain']),
                'givenName': display_name,
                'sn': display_name,
                'displayName': display_name,
                'mail': email or '',
                'accountExpires': '0',
            }
            if not conn.add(user_dn, attributes=attributes):
                raise UserError(_("Failed to create AD user %s:\n%s")
                                % (user_dn, conn.result))
            if not conn.extend.microsoft.modify_password(user_dn, password):
                raise UserError(_("User %s created but the password could not "
                                  "be set:\n%s") % (user_dn, conn.result))
            if not conn.modify(user_dn, {
                'userAccountControl': [(MODIFY_REPLACE, [UAC_NORMAL_ACCOUNT])],
            }):
                raise UserError(_("User %s created but could not be enabled:\n%s")
                                % (user_dn, conn.result))
            _logger.info("AD user created and enabled: %s", user_dn)
            return user_dn
        finally:
            conn.unbind()

    @api.model
    def add_user_to_group(self, user_dn, group_dn):
        if self._simulate():
            _logger.info("[SIMULATE] add %s to group %s", user_dn, group_dn)
            return True
        conn = self._connect()
        try:
            conn.modify(group_dn, {'member': [(MODIFY_ADD, [user_dn])]})
            # 68 = entryAlreadyExists: the user is already a member, not an error
            if conn.result['result'] not in (0, 68):
                raise UserError(_("Failed to add %s to %s:\n%s")
                                % (user_dn, group_dn, conn.result))
            return True
        finally:
            conn.unbind()

    @api.model
    def remove_user_from_groups(self, user_dn, group_dns):
        """Strip group memberships, always keeping Domain Users."""
        if self._simulate():
            _logger.info("[SIMULATE] remove %s from %s groups",
                         user_dn, len(group_dns))
            return True
        conn = self._connect()
        try:
            for group_dn in group_dns:
                if 'CN=Domain Users' in group_dn:
                    continue
                conn.modify(group_dn, {'member': [(MODIFY_DELETE, [user_dn])]})
                if conn.result['result'] != 0:
                    _logger.warning("Could not remove %s from %s: %s",
                                    user_dn, group_dn, conn.result)
            return True
        finally:
            conn.unbind()

    @api.model
    def move_user(self, sam_account_name, new_ou):
        """Move a user object to another OU. Returns the new DN."""
        if self._simulate():
            new_dn = 'CN=%s,%s' % (sam_account_name, new_ou)
            _logger.info("[SIMULATE] move %s to %s", sam_account_name, new_ou)
            return new_dn
        conn = self._connect()
        try:
            conn.search(
                search_base=self._get_settings()['base_dn'],
                search_filter='(sAMAccountName=%s)' % sam_account_name,
                search_scope=SUBTREE,
                attributes=['distinguishedName', 'cn'],
            )
            if not conn.entries:
                raise UserError(_("No AD user found with sAMAccountName '%s'.")
                                % sam_account_name)
            user_dn = conn.entries[0].distinguishedName.value
            user_cn = conn.entries[0].cn.value
            conn.modify_dn(dn=user_dn, relative_dn='CN=%s' % user_cn,
                           new_superior=new_ou)
            if conn.result['result'] != 0:
                raise UserError(_("Failed to move %s to %s:\n%s")
                                % (user_dn, new_ou, conn.result))
            new_dn = 'CN=%s,%s' % (user_cn, new_ou)
            _logger.info("AD user moved: %s -> %s", user_dn, new_dn)
            return new_dn
        finally:
            conn.unbind()

    @api.model
    def disable_user(self, sam_account_name):
        if self._simulate():
            _logger.info("[SIMULATE] disable AD user %s", sam_account_name)
            return True
        conn = self._connect()
        try:
            conn.search(
                search_base=self._get_settings()['base_dn'],
                search_filter='(sAMAccountName=%s)' % sam_account_name,
                search_scope=SUBTREE,
                attributes=['distinguishedName', 'userAccountControl'],
            )
            if not conn.entries:
                raise UserError(_("AD user '%s' not found.") % sam_account_name)
            user_dn = conn.entries[0].distinguishedName.value
            current_uac = int(conn.entries[0].userAccountControl.value)
            conn.modify(user_dn, {
                'userAccountControl': [
                    (MODIFY_REPLACE, [current_uac | UAC_ACCOUNTDISABLE])],
            })
            if conn.result['result'] != 0:
                raise UserError(_("Failed to disable %s:\n%s")
                                % (user_dn, conn.result))
            _logger.info("AD user disabled: %s", user_dn)
            return True
        finally:
            conn.unbind()

    @api.model
    def delete_user(self, sam_account_name):
        if self._simulate():
            _logger.info("[SIMULATE] delete AD user %s", sam_account_name)
            return True
        conn = self._connect()
        try:
            conn.search(
                search_base=self._get_settings()['base_dn'],
                search_filter='(sAMAccountName=%s)' % sam_account_name,
                search_scope=SUBTREE,
                attributes=['distinguishedName'],
            )
            if not conn.entries:
                raise UserError(_("AD user '%s' not found.") % sam_account_name)
            user_dn = conn.entries[0].distinguishedName.value
            if not conn.delete(user_dn):
                raise UserError(_("Failed to delete %s:\n%s")
                                % (user_dn, conn.result))
            _logger.info("AD user deleted: %s", user_dn)
            return True
        finally:
            conn.unbind()

    @api.model
    def set_password(self, sam_account_name, new_password):
        if self._simulate():
            _logger.info("[SIMULATE] reset password for %s", sam_account_name)
            return True
        conn = self._connect()
        try:
            user_dn = self.find_user_dn(sam_account_name)
            if not user_dn:
                raise UserError(_("AD user '%s' not found.") % sam_account_name)
            if not conn.extend.microsoft.modify_password(user_dn, new_password):
                raise UserError(_("Failed to reset the password:\n%s")
                                % conn.result)
            return True
        finally:
            conn.unbind()
