from odoo import models, api
import logging
import subprocess

_logger = logging.getLogger(__name__)


class SnmpMonitorService(models.Model):
    _name = 'snmp.monitor.service'
    _description = 'SNMP Monitor Service'

    def ping_device(self, ip_address):
        try:
            from icmplib import ping as icmp_ping
            result = icmp_ping(str(ip_address), count=1, timeout=2, privileged=False)
            return result.is_alive
        except Exception:
            pass
        try:
            cmd = ['ping', '-c', '1', '-W', '1', str(ip_address)]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
            return result.returncode == 0
        except Exception:
            return False

    def _build_snmp_args(self, device):
        if device.snmp_version == 'v3':
            return ['-v', '3', '-u', device.snmp_username or '',
                    '-l', 'authPriv', '-a', device.snmp_auth_protocol or 'MD5',
                    '-A', device.snmp_auth_password or '',
                    '-x', device.snmp_priv_protocol or 'DES',
                    '-X', device.snmp_priv_password or '']
        elif device.snmp_version == 'v1':
            return ['-v', '1', '-c', device.snmp_community or 'public']
        return ['-v', '2c', '-c', device.snmp_community or 'public']

    def _snmp_get_single(self, device, oid):
        try:
            args = self._build_snmp_args(device)
            target = f"{device.ip_address}:{device.snmp_port or 161}"
            # -t 5: wait 5s per try (was 2s, too short for internet-distance servers)
            # -r 2: retry twice (was 1)
            cmd = ['snmpget', '-Oqv', '-t', '5', '-r', '2'] + args + [target, oid]
            _logger.info("[SNMP TEST] Running command: %s", ' '.join(cmd))
            # subprocess timeout bumped from 5s -> 30s to give snmpget room to retry
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            _logger.info("[SNMP TEST] Return code: %s", result.returncode)
            _logger.info("[SNMP TEST] STDOUT: %r", result.stdout.decode().strip())
            _logger.info("[SNMP TEST] STDERR: %r", result.stderr.decode().strip())
            if result.returncode == 0:
                value = result.stdout.decode().strip().strip('"')
                return value if value else None
        except subprocess.TimeoutExpired as e:
            _logger.error("[SNMP TEST] subprocess.TimeoutExpired after 30s: %s", e)
        except Exception as e:
            _logger.error("[SNMP TEST] snmpget exception for %s: %s", oid, e)
        return None

    def test_snmp_connection(self, device):
        val = self._snmp_get_single(device, '1.3.6.1.2.1.1.1.0')
        return val is not None

    def get_system_info(self, device):
        oids = {
            'sysDescr':    '1.3.6.1.2.1.1.1.0',
            'sysUpTime':   '1.3.6.1.2.1.1.3.0',
            'sysContact':  '1.3.6.1.2.1.1.4.0',
            'sysName':     '1.3.6.1.2.1.1.5.0',
            'sysLocation': '1.3.6.1.2.1.1.6.0',
        }
        return {key: self._snmp_get_single(device, oid) or False for key, oid in oids.items()}

    def get_interface_list(self, device):
        try:
            args = self._build_snmp_args(device)
            target = f"{device.ip_address}:{device.snmp_port or 161}"
            cmd = ['snmpwalk', '-Oqn', '-t', '3', '-r', '1'] + args + [target, '1.3.6.1.2.1.2.2.1']
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if result.returncode != 0:
                return []
            interfaces = {}
            columns = {'1': 'index', '2': 'descr', '3': 'type', '4': 'mtu',
                       '5': 'speed', '6': 'phys_addr', '7': 'admin_status',
                       '8': 'oper_status', '10': 'bytes_in', '16': 'bytes_out',
                       '11': 'pkts_in', '17': 'pkts_out', '14': 'err_in', '20': 'err_out'}
            for line in result.stdout.decode().splitlines():
                line = line.strip()
                if not line or ' ' not in line:
                    continue
                oid_part, _, val = line.partition(' ')
                parts = oid_part.strip('.').split('.')
                if len(parts) < 2:
                    continue
                col_idx, if_idx = parts[-2], parts[-1]
                if if_idx not in interfaces:
                    interfaces[if_idx] = {'index': if_idx}
                if col_idx in columns:
                    interfaces[if_idx][columns[col_idx]] = val.strip().strip('"')
            return list(interfaces.values())
        except Exception as e:
            _logger.error(f"snmpwalk error: {e}")
            return []

    def _detect_device_vendor(self, device):
        """Detect vendor from device name/notes/type to pick correct SNMP OIDs."""
        name = (device.name or '').lower()
        notes = (device.notes or '').lower()
        combined = name + ' ' + notes

        if 'sophos' in combined or device.device_type == 'firewall':
            return 'sophos'
        if 'cbs350' in combined or 'cbs' in combined or 'gigabit managed switch' in combined:
            return 'cbs350'
        if device.manufacturer == 'cisco' or 'cisco' in combined:
            return 'cisco'
        if device.manufacturer == 'mikrotik' or 'mikrotik' in combined or 'routeros' in combined:
            return 'mikrotik'
        return 'generic'

    def get_device_metrics(self, device):
        results = {
            'cpu_usage': 0.0,
            'memory_usage': 0.0,
            'memory_total': 0.0,
            'memory_used': 0.0,
            'ram_total': 0.0,    # Total physical RAM in GB
            'ram_used': 0.0,     # Used physical RAM in GB
        }

        metric_oids = {
            # Sophos XGS Firewall — all confirmed from real SNMP walk
            'sophos': {
                'cpu':           '1.3.6.1.4.1.2604.5.1.2.4.2.0',   # CPU %
                'mem_pct':       '1.3.6.1.4.1.2604.5.1.2.5.2.0',   # Memory %
                'ram_total':     '1.3.6.1.4.1.2604.5.1.2.4.1.0',   # Total RAM MB (34536 = 33.7GB)
                'ram_used_base': '1.3.6.1.4.1.2604.5.1.2.5.1.0',   # Total process mem MB
                'ram_free':      '1.3.6.1.4.1.2604.5.1.2.5.3.0',   # Free RAM MB
            },
            # Cisco CBS350 Switch
            'cbs350': {
                'cpu':      '1.3.6.1.4.1.9.6.1.101.1.2.0',    # CPU %
                'mem_pct':  '1.3.6.1.4.1.9.6.1.101.1.8.0',    # Memory %
                # No RAM OID on CBS350
            },
            # Cisco IOS
            'cisco': {
                'cpu':      '1.3.6.1.4.1.9.9.109.1.1.1.1.7.1',
                'mem_used': '1.3.6.1.4.1.9.9.48.1.1.1.5.1',
                'mem_free': '1.3.6.1.4.1.9.9.48.1.1.1.6.1',
            },
            # MikroTik RouterOS
            'mikrotik': {
                'cpu':       '1.3.6.1.4.1.14988.1.1.3.10.0',
                'mem_total': '1.3.6.1.4.1.14988.1.1.3.8.0',
                'mem_used':  '1.3.6.1.4.1.14988.1.1.3.11.0',
            },
            # Generic
            'generic': {
                'cpu': '1.3.6.1.2.1.25.3.3.1.2.1',
            }
        }

        vendor = self._detect_device_vendor(device)
        m_oids = metric_oids.get(vendor, metric_oids['generic'])

        try:
            # CPU
            if 'cpu' in m_oids:
                val = self._snmp_get_single(device, m_oids['cpu'])
                if val:
                    try:
                        results['cpu_usage'] = float(val)
                    except ValueError:
                        pass

            # Memory — direct % (Sophos / CBS350)
            if 'mem_pct' in m_oids:
                val = self._snmp_get_single(device, m_oids['mem_pct'])
                if val:
                    try:
                        results['memory_usage'] = float(val)
                        results['memory_used'] = float(val)
                        results['memory_total'] = 100.0
                    except ValueError:
                        pass

            # Memory — used/free bytes (Cisco IOS)
            elif 'mem_used' in m_oids and 'mem_free' in m_oids and 'mem_total' not in m_oids:
                used = self._snmp_get_single(device, m_oids['mem_used'])
                free = self._snmp_get_single(device, m_oids['mem_free'])
                if used and free:
                    try:
                        u, f = float(used), float(free)
                        results['memory_used'] = u / (1024 * 1024)
                        results['memory_total'] = (u + f) / (1024 * 1024)
                        results['memory_usage'] = (u / (u + f)) * 100
                    except ValueError:
                        pass

            # Memory — total/used bytes (MikroTik)
            elif 'mem_total' in m_oids and 'mem_used' in m_oids:
                total = self._snmp_get_single(device, m_oids['mem_total'])
                used = self._snmp_get_single(device, m_oids['mem_used'])
                if total and used:
                    try:
                        t, u = float(total), float(used)
                        results['memory_total'] = t / (1024 * 1024)
                        results['memory_used'] = u / (1024 * 1024)
                        results['memory_usage'] = (u / t) * 100 if t > 0 else 0.0
                    except ValueError:
                        pass

            # Physical RAM — Sophos only (Total/Used in GB)
            if 'ram_total' in m_oids:
                ram_total = self._snmp_get_single(device, m_oids['ram_total'])
                ram_free = self._snmp_get_single(device, m_oids['ram_free'])
                ram_used_base = self._snmp_get_single(device, m_oids['ram_used_base'])
                if ram_total and ram_free and ram_used_base:
                    try:
                        t = float(ram_total)        # MB
                        f = float(ram_free)          # MB
                        u_base = float(ram_used_base)  # MB
                        used = u_base - f            # MB used
                        results['ram_total'] = round(t / 1024, 1)   # GB
                        results['ram_used'] = round(used / 1024, 1)  # GB
                    except ValueError:
                        pass

            _logger.debug(
                f"[Metrics] {device.name} ({vendor}) → "
                f"CPU={results['cpu_usage']}% MEM={results['memory_usage']}% "
                f"RAM={results['ram_used']}GB/{results['ram_total']}GB"
            )

        except Exception as e:
            _logger.error(f"Metrics error for {device.name}: {e}")

        return results

    def check_all_devices(self):
        _logger.info("Starting bulk network device status check")
        devices = self.env['asset.network.device'].search([('is_active', '=', True)])
        for device in devices:
            try:
                device.check_device_status()
            except Exception as e:
                _logger.error(f"Error checking {device.device_code}: {e}")
        _logger.info(f"Status check done for {len(devices)} devices")