from odoo import models, fields, api

class NetworkDeviceInterface(models.Model):
    _name = 'network.device.interface'
    _description = 'Network Device Interface'

    name = fields.Char(string="Interface Name", required=True)
    device_id = fields.Many2one('asset.network.device', string="Network Device", required=True, ondelete='cascade')
    interface_index = fields.Integer(string="SNMP Interface Index")
    interface_type = fields.Char(string="Interface Type")
    mac_address = fields.Char(string="MAC Address")
    ip_address = fields.Char(string="IP Address")
    subnet_mask = fields.Char(string="Subnet Mask")
    admin_status = fields.Selection([
        ('up', 'Up'),
        ('down', 'Down'),
        ('testing', 'Testing')
    ], string="Admin Status")
    oper_status = fields.Selection([
        ('up', 'Up'),
        ('down', 'Down'),
        ('testing', 'Testing'),
        ('unknown', 'Unknown'),
        ('dormant', 'Dormant'),
        ('notPresent', 'Not Present'),
        ('lowerLayerDown', 'Lower Layer Down')
    ], string="Operational Status")
    speed = fields.Char(string="Speed")
    mtu = fields.Integer(string="MTU")
    last_change = fields.Datetime(string="Last Status Change")

    # Traffic Stats
    bytes_in = fields.Float(string="Bytes In")
    bytes_out = fields.Float(string="Bytes Out")
    packets_in = fields.Float(string="Packets In")
    packets_out = fields.Float(string="Packets Out")
    errors_in = fields.Integer(string="Input Errors")
    errors_out = fields.Integer(string="Output Errors")
    
    last_bytes_in = fields.Float(string="Last Bytes In")
    last_bytes_out = fields.Float(string="Last Bytes Out")
    last_stats_update = fields.Datetime(string="Last Stats Update")
    
    bandwidth_usage = fields.Float(string="Bandwidth Usage %", compute="_compute_bandwidth_usage", store=True)

    # General
    is_active = fields.Boolean(string="Active", default=True)
    description = fields.Text(string="Description")

    @api.depends('bytes_in', 'bytes_out', 'last_bytes_in', 'last_bytes_out', 'last_stats_update')
    def _compute_bandwidth_usage(self):
        for record in self:
            if not record.last_stats_update or not record.speed:
                record.bandwidth_usage = 0.0
                continue
                
            try:
                # Calculate time delta in seconds
                now = fields.Datetime.now()
                delta_time = (now - record.last_stats_update).total_seconds()
                
                if delta_time <= 0:
                    record.bandwidth_usage = 0.0
                    continue
                
                # Bytes to bits (x8)
                delta_bytes = (record.bytes_in - record.last_bytes_in) + (record.bytes_out - record.last_bytes_out)
                if delta_bytes < 0: # Counter wrap around or reset
                    record.bandwidth_usage = 0.0
                    continue
                    
                bits_per_sec = (delta_bytes * 8) / delta_time
                
                # Interface speed is usually in bps as string like "1000000000" (1Gbps)
                speed_bps = float(record.speed) if record.speed.isdigit() else 0.0
                
                if speed_bps > 0:
                    record.bandwidth_usage = min((bits_per_sec / speed_bps) * 100, 100.0)
                else:
                    record.bandwidth_usage = 0.0
            except:
                record.bandwidth_usage = 0.0
