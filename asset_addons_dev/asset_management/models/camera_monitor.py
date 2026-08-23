# -*- coding: utf-8 -*-

from odoo import models, fields, api
import logging
import subprocess
import requests
import platform

_logger = logging.getLogger(__name__)


class CameraMonitorService(models.Model):
    """Camera Monitoring Service for CCTV Management"""
    
    _name = 'camera.monitor.service'
    _description = 'Camera Monitor Service'
    
    name = fields.Char(
        string="Service Name",
        default="Camera Monitor Service"
    )
    
    def ping_camera(self, ip_address):
        """
        Ping camera IP address to check network connectivity
        
        Args:
            ip_address (str): IP address to ping
            
        Returns:
            bool: True if ping successful, False otherwise
        """
        try:
            os_system = platform.system()
            
            if os_system == 'Windows':
                command = ['ping', '-n', '1', '-w', '1000', ip_address]
            else:
                command = ['ping', '-c', '1', '-W', '1', ip_address]
            
            _logger.info(f"Pinging camera at {ip_address} on {os_system}")
            
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3
            )
            
            if result.returncode == 0:
                _logger.info(f"Ping successful for {ip_address}")
                return True
            else:
                _logger.warning(f"Ping failed for {ip_address}")
                return False
                
        except subprocess.TimeoutExpired:
            _logger.error(f"Ping timeout for {ip_address}")
            return False
            
        except Exception as e:
            _logger.error(f"Error pinging {ip_address}: {str(e)}")
            return False
    
    def check_http_access(self, url, username=None, password=None):
        """
        Check HTTP access to camera web interface
        
        Args:
            url (str): HTTP URL to check
            username (str, optional): Authentication username
            password (str, optional): Authentication password
            
        Returns:
            int: HTTP status code or 0 on error
        """
        try:
            _logger.info(f"Checking HTTP access to {url}")
            
            auth = None
            if username and password:
                auth = (username, password)
                _logger.debug(f"Using authentication for {url}")
            
            response = requests.get(
                url,
                auth=auth,
                timeout=5,
                verify=False
            )
            
            status_code = response.status_code
            _logger.info(f"HTTP access to {url} returned status {status_code}")
            return status_code
            
        except requests.exceptions.Timeout:
            _logger.error(f"HTTP timeout for {url}")
            return 0
            
        except requests.exceptions.ConnectionError:
            _logger.error(f"Connection error for {url}")
            return 0
            
        except Exception as e:
            _logger.error(f"Error accessing {url}: {str(e)}")
            return 0
    
    def check_all_cameras(self):
        """
        Check status of all active cameras
        
        Returns:
            dict: Summary of check results
        """
        _logger.info("Starting bulk camera status check")
        
        try:
            cameras = self.env['asset.camera'].search([('is_active', '=', True)])
            total_cameras = len(cameras)
            
            _logger.info(f"Found {total_cameras} active cameras to check")
            
            online_count = 0
            offline_count = 0
            error_count = 0
            
            for camera in cameras:
                try:
                    result = camera.check_camera_status()
                    
                    if result:
                        online_count += 1
                    else:
                        if camera.stream_status == 'error':
                            error_count += 1
                        else:
                            offline_count += 1
                            
                except Exception as e:
                    error_count += 1
                    _logger.error(f"Error checking camera {camera.camera_code}: {str(e)}")
            
            summary = {
                'total': total_cameras,
                'online': online_count,
                'offline': offline_count,
                'error': error_count
            }
            
            _logger.info(
                f"Camera check completed - Total: {total_cameras}, "
                f"Online: {online_count}, Offline: {offline_count}, Error: {error_count}"
            )
            
            return summary
            
        except Exception as e:
            _logger.error(f"Error in check_all_cameras: {str(e)}")
            return {
                'total': 0,
                'online': 0,
                'offline': 0,
                'error': 0
            }
    
    def get_camera_statistics(self):
        """
        Get camera statistics and status counts
        
        Returns:
            dict: Camera statistics with counts
        """
        try:
            _logger.info("Retrieving camera statistics")
            
            total_cameras = self.env['asset.camera'].search_count([])
            online_cameras = self.env['asset.camera'].search_count([
                ('stream_status', '=', 'online')
            ])
            offline_cameras = self.env['asset.camera'].search_count([
                ('stream_status', '=', 'offline')
            ])
            error_cameras = self.env['asset.camera'].search_count([
                ('stream_status', '=', 'error')
            ])
            unknown_cameras = self.env['asset.camera'].search_count([
                ('stream_status', '=', 'unknown')
            ])
            active_cameras = self.env['asset.camera'].search_count([
                ('is_active', '=', True)
            ])
            inactive_cameras = self.env['asset.camera'].search_count([
                ('is_active', '=', False)
            ])
            
            statistics = {
                'total': total_cameras,
                'online': online_cameras,
                'offline': offline_cameras,
                'error': error_cameras,
                'unknown': unknown_cameras,
                'active': active_cameras,
                'inactive': inactive_cameras,
                'uptime_percentage': round((online_cameras / total_cameras * 100), 2) if total_cameras > 0 else 0.0
            }
            
            _logger.info(f"Camera statistics: {statistics}")
            
            return statistics
            
        except Exception as e:
            _logger.error(f"Error getting camera statistics: {str(e)}")
            return {
                'total': 0,
                'online': 0,
                'offline': 0,
                'error': 0,
                'unknown': 0,
                'active': 0,
                'inactive': 0,
                'uptime_percentage': 0.0
            }

    def create_event(self, camera_id, event_type, message, severity='info'):
        """Helper method to create camera events"""
        event_model = self.env['camera.event']
        return event_model.create({
            'camera_id': camera_id,
            'event_type': event_type,
            'event_message': message,
            'severity': severity,
            'event_time': fields.Datetime.now()
        })


class CameraEvent(models.Model):
    """Track camera events (offline, motion, recording changes, errors)"""
    _name = 'camera.event'
    _description = 'Camera Event'
    _order = 'event_time desc'

    camera_id = fields.Many2one('asset.camera', string='Camera', required=True, ondelete='cascade')
    event_type = fields.Selection([
        ('offline', 'Camera Offline'),
        ('online', 'Camera Online'),
        ('motion', 'Motion Detected'),
        ('recording_started', 'Recording Started'),
        ('recording_stopped', 'Recording Stopped'),
        ('stream_error', 'Stream Error'),
        ('storage_warning', 'Storage Warning'),
        ('storage_critical', 'Storage Critical')
    ], string='Event Type', required=True)
    event_message = fields.Text(string='Event Message')
    event_time = fields.Datetime(string='Event Time', default=fields.Datetime.now, required=True)
    severity = fields.Selection([
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('critical', 'Critical')
    ], string='Severity', required=True)
    is_acknowledged = fields.Boolean(string='Acknowledged', default=False)
    acknowledged_by = fields.Many2one('res.users', string='Acknowledged By')
    acknowledged_time = fields.Datetime(string='Acknowledged Time')

    # ==========================================
    # TASK 4: ALERT LIFECYCLE FIELDS
    # ==========================================
    resolved = fields.Boolean(
        string='Resolved',
        default=False,
        help="Whether this event has been resolved (e.g., camera came back online)"
    )
    resolved_time = fields.Datetime(
        string='Resolved Time',
        help="Timestamp when the event was auto-resolved"
    )

    def acknowledge_alert(self):
        """Acknowledge the camera event/alert"""
        for record in self:
            record.write({
                'is_acknowledged': True,
                'acknowledged_by': self.env.user.id,
                'acknowledged_time': fields.Datetime.now()
            })

    @api.model
    def auto_resolve_offline_events(self, camera_id):
        """
        Auto-resolve previous offline events when camera comes back online.

        TASK 4: Alert Lifecycle - auto-resolve previous offline events.
        Called when camera status changes to online.

        Args:
            camera_id (int): ID of the camera that came back online

        Returns:
            int: Number of events resolved
        """
        # Find unresolved offline events for this camera
        unresolved_events = self.search([
            ('camera_id', '=', camera_id),
            ('event_type', '=', 'offline'),
            ('resolved', '=', False)
        ])

        if unresolved_events:
            unresolved_events.write({
                'resolved': True,
                'resolved_time': fields.Datetime.now()
            })
            _logger.info(
                f"Auto-resolved {len(unresolved_events)} offline events for camera ID {camera_id}"
            )

        return len(unresolved_events)
