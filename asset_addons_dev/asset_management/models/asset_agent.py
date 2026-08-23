# -*- coding: utf-8 -*-
"""
Enterprise Agent Identity Model

This model provides centralized agent identity management for the
Asset Management system. It tracks registered agents, their authentication
tokens, and online/offline status.

TASK 2: Add Enterprise Agent Identity
"""

from odoo import models, fields, api
from odoo.exceptions import ValidationError
import uuid
import logging

_logger = logging.getLogger(__name__)


class AssetAgent(models.Model):
    """
    Enterprise Agent Identity Model

    Tracks registered monitoring agents (CCTV, laptop, etc.) with their
    identity, authentication tokens, and heartbeat status.
    """

    _name = 'asset.agent'
    _description = 'Monitoring Agent Identity'
    _order = 'last_seen desc'
    _rec_name = 'agent_id'

    # ==========================================
    # CORE IDENTITY FIELDS (TASK 2 - EXACT)
    # ==========================================

    agent_id = fields.Char(
        string="Agent ID",
        required=True,
        readonly=True,
        copy=False,
        index=True,
        help="Unique UUID identifier for this agent"
    )

    hostname = fields.Char(
        string="Hostname",
        help="Agent machine hostname"
    )

    platform = fields.Selection(
        selection=[
            ('windows', 'Windows'),
            ('linux', 'Linux'),
            ('macos', 'macOS'),
            ('cctv', 'CCTV')
        ],
        string="Platform",
        help="Operating system or agent type"
    )

    token = fields.Char(
        string="Authentication Token",
        readonly=True,
        copy=False,
        help="Secret token for agent authentication (future use)"
    )

    last_seen = fields.Datetime(
        string="Last Seen",
        help="Timestamp of last agent heartbeat/sync"
    )

    status = fields.Selection(
        selection=[
            ('online', 'Online'),
            ('offline', 'Offline')
        ],
        string="Status",
        default='offline',
        help="Current agent connectivity status"
    )

    # ==========================================
    # ADDITIONAL ENTERPRISE FIELDS
    # ==========================================

    agent_version = fields.Char(
        string="Agent Version",
        help="Version of the agent software"
    )

    ip_address = fields.Char(
        string="IP Address",
        help="Last known IP address of the agent"
    )

    registered_at = fields.Datetime(
        string="Registered At",
        readonly=True,
        default=fields.Datetime.now,
        help="When the agent was first registered"
    )

    notes = fields.Text(
        string="Notes",
        help="Additional notes about this agent"
    )

    os_version = fields.Char(
        string="OS Version",
        help="Operating system version"
    )

    department_id = fields.Many2one(
        'hr.department',
        string='Department',
        help='Department this device belongs to'
    )

    # =========================================================================
    # RELATED FIELDS FOR WIZARD TREE VIEW
    # =========================================================================
    device_name = fields.Char(
        string='Device Name',
        related='hostname',
        store=True,
        readonly=True,
        help='Device hostname'
    )
    serial_number = fields.Char(
        string='Serial Number',
        related='agent_id',
        store=True,
        readonly=True,
        help='Unique agent identifier'
    )

    # ==========================================
    # SQL CONSTRAINTS -> @api.constrains
    # ==========================================

    @api.constrains('agent_id')
    def _check_agent_id_unique(self):
        for record in self:
            if self.search_count([('agent_id', '=', record.agent_id), ('id', '!=', record.id)]) > 0:
                raise ValidationError(_('Agent ID must be unique!'))

    @api.constrains('token')
    def _check_token_unique(self):
        for record in self:
            if record.token and self.search_count([('token', '=', record.token), ('id', '!=', record.id)]) > 0:
                raise ValidationError(_('Token must be unique!'))

    # ==========================================
    # HELPER METHODS (TASK 3)
    # ==========================================

    def mark_agent_online(self):
        """
        Mark agent as online and update last_seen timestamp.

        TASK 3: Simple helper method for heartbeat handling.
        Called when agent sends heartbeat or telemetry data.
        """
        self.ensure_one()
        self.write({
            'status': 'online',
            'last_seen': fields.Datetime.now()
        })
        _logger.info(f"Agent {self.agent_id} marked as ONLINE")
        return True

    def mark_agent_offline(self):
        """
        Mark agent as offline.

        TASK 3: Simple helper method for status management.
        Called by cron or when agent fails to respond.
        """
        self.ensure_one()
        self.write({
            'status': 'offline'
        })
        _logger.info(f"Agent {self.agent_id} marked as OFFLINE")
        return True

    @api.model
    def register_agent(self, hostname=None, platform=None, agent_version=None, ip_address=None):
        """
        Register a new agent or update existing one.

        TASK 2: Agent registration logic.
        - Creates new agent record if hostname doesn't exist
        - Generates UUID agent_id and token
        - Updates last_seen on every call

        Args:
            hostname (str): Agent machine hostname
            platform (str): Platform type (windows/linux/macos/cctv)
            agent_version (str): Agent software version
            ip_address (str): Agent IP address

        Returns:
            dict: Agent registration result with agent_id and token
        """
        # Search for existing agent by hostname
        existing_agent = None
        if hostname:
            existing_agent = self.search([('hostname', '=', hostname)], limit=1)

        if existing_agent:
            # Update existing agent
            update_vals = {
                'last_seen': fields.Datetime.now(),
                'status': 'online'
            }
            if platform:
                update_vals['platform'] = platform
            if agent_version:
                update_vals['agent_version'] = agent_version
            if ip_address:
                update_vals['ip_address'] = ip_address

            existing_agent.write(update_vals)

            _logger.info(f"Agent {existing_agent.agent_id} updated (hostname: {hostname})")

            return {
                'success': True,
                'agent_id': existing_agent.agent_id,
                'token': existing_agent.token,
                'message': 'Agent updated successfully'
            }

        # Create new agent
        new_agent_id = str(uuid.uuid4())
        new_token = str(uuid.uuid4())

        new_agent = self.create({
            'agent_id': new_agent_id,
            'token': new_token,
            'hostname': hostname or 'Unknown',
            'platform': platform or 'cctv',
            'agent_version': agent_version,
            'ip_address': ip_address,
            'status': 'online',
            'last_seen': fields.Datetime.now(),
            'registered_at': fields.Datetime.now()
        })

        _logger.info(f"New agent registered: {new_agent_id} (hostname: {hostname})")

        return {
            'success': True,
            'agent_id': new_agent_id,
            'token': new_token,
            'message': 'Agent registered successfully'
        }

    @api.model
    def get_agent_by_id(self, agent_id):
        """
        Find agent by agent_id.

        Args:
            agent_id (str): UUID agent identifier

        Returns:
            recordset: Agent record or empty recordset
        """
        return self.search([('agent_id', '=', agent_id)], limit=1)

    @api.model
    def update_agent_heartbeat(self, agent_id, hostname=None, agent_version=None, ip_address=None):
        """
        Update agent heartbeat and metadata.

        TASK 3: Heartbeat handling - updates last_seen and stores agent info.

        Args:
            agent_id (str): UUID agent identifier
            hostname (str): Agent hostname
            agent_version (str): Agent software version
            ip_address (str): Agent IP address

        Returns:
            bool: True if successful, False otherwise
        """
        agent = self.get_agent_by_id(agent_id)

        if not agent:
            _logger.warning(f"Agent heartbeat received for unknown agent_id: {agent_id}")
            return False

        update_vals = {
            'last_seen': fields.Datetime.now(),
            'status': 'online'
        }

        if hostname:
            update_vals['hostname'] = hostname
        if agent_version:
            update_vals['agent_version'] = agent_version
        if ip_address:
            update_vals['ip_address'] = ip_address

        agent.write(update_vals)

        _logger.debug(f"Agent {agent_id} heartbeat updated")
        return True

    # =========================================================================
    # DISPLAY NAME WITH ONLINE INDICATOR
    # =========================================================================
    @api.depends("hostname", "device_name", "agent_id", "last_seen", "department_id")
    def _compute_display_name(self):
        """
        Show an online/offline indicator in the display name.
        Format: "● LAPTOP-DEV-001 — John Smith" (online) or "○ LAPTOP-FIN-009 — Robert Taylor" (offline)

        Odoo 19: name_get() was removed in favour of _compute_display_name().
        """
        for device in self:
            # Check if device is online (polled within last 5 minutes)
            online = False
            if device.last_seen:
                time_diff = (fields.Datetime.now() - device.last_seen).total_seconds()
                online = time_diff < 300

            indicator = '●' if online else '○'
            
            # Get assigned user name if available
            user_name = ''
            if hasattr(device, 'assigned_user_id') and device.assigned_user_id:
                user_name = f" — {device.assigned_user_id.name}"
            elif hasattr(device, 'employee_id') and device.employee_id:
                user_name = f" — {device.employee_id.name}"
            elif device.department_id:
                user_name = f" — {device.department_id.name}"
            else:
                user_name = ' — Unassigned'

            label = device.hostname or device.device_name or device.agent_id or 'Unknown'
            device.display_name = f"{indicator} {label}{user_name}"
