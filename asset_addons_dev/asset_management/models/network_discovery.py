from odoo import models, fields, api
import logging
import re
import socket
import subprocess
import ipaddress

_logger = logging.getLogger(__name__)

SNMP_AVAILABLE = False  # Using subprocess snmpget instead

# Standard SNMP OIDs for fingerprinting
OID_SYS_DESCR    = '1.3.6.1.2.1.1.1.0'
OID_SYS_NAME     = '1.3.6.1.2.1.1.5.0'
OID_SYS_LOCATION = '1.3.6.1.2.1.1.6.0'
OID_SYS_UPTIME   = '1.3.6.1.2.1.1.3.0'
OID_SYS_CONTACT  = '1.3.6.1.2.1.1.4.0'

# Device type fingerprint keywords — used to identify what kind of device
# answered SNMP. Order in DEVICE_FINGERPRINTS matters: first match wins.
DEVICE_FINGERPRINTS = {
    'firewall': [
        'firewall', 'asa', 'fortigate', 'palo alto', 'checkpoint',
        'sonicwall', 'watchguard', 'iptables', 'netscreen', 'juniper srx',
    ],
    'switch': [
        'switch', 'catalyst', 'nexus', 'procurve', 'powerconnect',
        'stackable', 'layer 2', 'layer 3', 'vlan', 'spanning tree',
    ],
    'access_point': [
        'access point', 'wireless', 'aironet', 'unifi', 'aruba',
        'wifi', 'wlan', 'ap ', '802.11', 'meraki',
    ],
    'printer': [
        # Printer-specific keywords found in SNMP sysDescr
        'laserjet', 'officejet', 'deskjet', 'inkjet', 'photosmart',
        'imageclass', 'imagerunner', 'pixma', 'maxify',
        'workforce', 'ecotank', 'expression', 'workgroup',
        'phaser', 'workcentre', 'altalink', 'versalink',
        'magicolor', 'bizhub', 'aficio',
        # Generic printer language
        'printer', 'mfp', 'multifunction', 'all-in-one',
        # Brand-specific printer model patterns
        'hp pagewide', 'canon ir', 'brother mfc', 'brother dcp',
        'epson l', 'samsung clp', 'samsung clx', 'samsung scx',
        'kyocera ecosys', 'kyocera taskalfa', 'oki c', 'lexmark',
        'ricoh aficio', 'konica minolta',
    ],
    'tldr': [
        # NVR / DVR / video recorder identifiers
        ' nvr ', 'nvr-', 'nvr/', ' dvr ', 'dvr-', 'dvr/', ' xvr ', 'xvr-',
        'network video recorder', 'digital video recorder',
        'video recorder', 'video surveillance', 'surveillance recorder',
        # Brand model patterns (Hikvision, Dahua, etc.)
        'ds-7', 'ds-8', 'ds-9',     # Hikvision NVRs/DVRs
        'hikvision nvr', 'hikvision dvr',
        'dahua nvr', 'dahua dvr', 'dh-nvr', 'dh-dvr', 'dh-xvr',
        'uniview nvr', 'uniview dvr',
        'cp plus', 'axis camera station',
    ],
    'router': [
        'router', 'cisco ios', 'junos', 'routeros', 'mikrotik',
        'edge', 'gateway', 'broadband', 'dsl', 'wan',
    ],
}

# Manufacturer fingerprint keywords
MANUFACTURER_FINGERPRINTS = {
    # Network gear brands
    'cisco':    ['cisco', 'catalyst', 'aironet', 'nexus', 'asa'],
    'juniper':  ['juniper', 'junos', 'srx', 'ex series'],
    'hp':       ['hp ', 'hewlett', 'procurve', 'aruba', 'laserjet',
                 'officejet', 'deskjet', 'pagewide', 'photosmart'],
    'mikrotik': ['mikrotik', 'routeros'],
    'ubiquiti': ['ubiquiti', 'unifi', 'edgeos'],
    'tp_link':  ['tp-link', 'tplink'],
    'dell':     ['dell', 'powerconnect', 'force10'],
    'fortinet': ['fortinet', 'fortigate'],
    # Printer brands
    'canon':          ['canon', 'imageclass', 'imagerunner', 'pixma', 'maxify', 'canon ir'],
    'brother':        ['brother', 'brother mfc', 'brother dcp', 'brother hl'],
    'epson':          ['epson', 'workforce', 'ecotank', 'expression'],
    'samsung':        ['samsung clp', 'samsung clx', 'samsung scx', 'samsung xpress'],
    'kyocera':        ['kyocera', 'ecosys', 'taskalfa'],
    'lexmark':        ['lexmark'],
    'ricoh':          ['ricoh', 'aficio'],
    'konica_minolta': ['konica', 'minolta', 'bizhub', 'magicolor'],
    'xerox':          ['xerox', 'phaser', 'workcentre', 'altalink', 'versalink'],
    'oki':            ['oki ', 'oki data', 'oki c', 'okidata'],
    # NVR / DVR brands
    'hikvision':      ['hikvision', 'ds-7', 'ds-8', 'ds-9'],
    'dahua':          ['dahua', 'dh-nvr', 'dh-dvr', 'dh-xvr'],
    'uniview':        ['uniview', 'unv'],
    'cp_plus':        ['cp plus', 'cp-plus'],
    'axis':           ['axis camera', 'axis communications'],
}


class NetworkDiscoveryService(models.Model):
    _name = 'network.discovery.service'
    _description = 'Network Auto Discovery Service'

    # ─── Config fields (single record) ───────────────────────────────────────
    name = fields.Char(default='Network Discovery Config')
    subnet = fields.Char(
        string='Subnet to Scan',
        default='192.168.1.0/24',
        help='CIDR notation e.g. 192.168.105.0/24'
    )
    snmp_community = fields.Char(default='public')
    snmp_port = fields.Integer(default=161)
    scan_threads = fields.Integer(default=50)
    auto_create = fields.Boolean(
        default=True,
        string='Auto-create new devices',
        help='Automatically create records for newly discovered devices'
    )
    last_scan = fields.Datetime(readonly=True)
    discovered_count = fields.Integer(readonly=True)

    # ─── Ping ────────────────────────────────────────────────────────────────
    def _ping(self, ip):
        """Fast LAN ping. Returns True if the host responded within 1s.

        On a LAN, anything alive responds in <100ms; we use 1s as the cutoff
        to keep /24 scans bounded. Falls back to system `ping` if icmplib
        isn't available.
        """
        try:
            from icmplib import ping as icmp_ping
            result = icmp_ping(str(ip), count=1, timeout=1, privileged=False)
            return result.is_alive
        except Exception as e:
            _logger.debug(f"icmplib ping failed for {ip}: {e}, trying subprocess...")

        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '1', str(ip)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=2
            )
            return result.returncode == 0
        except Exception:
            return False

    def _ping_with_ttl(self, ip):
        """Ping and also return the reply's TTL, for OS-family guessing.

        icmplib's summary object does not expose TTL in the version this
        module targets, so this always shells out to the system `ping` and
        regexes "ttl=NN" out of stdout. The TTL is a property of the reply
        packet from the TARGET, not of whatever OS is running Odoo, so this
        works regardless of the server's own platform.

        Returns (is_alive: bool, ttl: int | None).
        """
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '1', str(ip)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=2
            )
            if result.returncode != 0:
                return False, None
            match = re.search(rb'ttl=(\d+)', result.stdout, re.IGNORECASE)
            return True, (int(match.group(1)) if match else None)
        except Exception as e:
            _logger.debug(f"ping-with-ttl failed for {ip}: {e}")
            return False, None

    def _guess_os_from_ttl(self, ttl):
        """Bucket a reply TTL into a rough OS family.

        Operating systems set a fixed starting TTL (64, 128 or 255) and each
        router hop decrements it by one. On a single LAN segment the
        observed value is usually the starting value or very close to it.

        This is a WELL-KNOWN HEURISTIC, not identification:
          - ~64  is Linux, macOS, iOS AND Android - it does not distinguish
            a laptop from a phone.
          - ~128 is Windows.
          - ~255 is the common default for Cisco and many embedded/router
            firmwares, but plenty of Linux-based routers also use 64.
        Combine with the port scan before trusting this.
        """
        if not ttl:
            return 'unknown'
        if ttl > 200:
            return 'network_device'
        if ttl > 100:
            return 'windows'
        if ttl > 30:
            return 'linux_mac_mobile'
        return 'unknown'

    def _scan_ports(self, ip_str, ports, timeout=0.25):
        """Try to open a TCP connection to each port. Returns the open subset.

        This is a signature probe of a handful of well-known ports, not a
        real port scan - it exists only to disambiguate the TTL guess, e.g.
        confirm SMB/RDP (Windows) or SSH (Linux/macOS/some network gear).
        """
        open_ports = []
        for p in ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                if sock.connect_ex((ip_str, p)) == 0:
                    open_ports.append(p)
                sock.close()
            except Exception:
                continue
        return open_ports

    def _get_mac_address(self, ip_str):
        """Look up the MAC address for `ip_str` from the kernel ARP table.

        Reads /proc/net/arp directly rather than shelling out to `arp -a`,
        which is not installed on minimal Docker images. Only works for hosts
        on the same L2 segment as the Odoo server - a host behind a router
        hop will not have an ARP entry, and this silently returns None rather
        than raising, since that is an expected case, not an error.

        Caveat this deliberately does NOT hide: many phones randomise their
        MAC address per Wi-Fi network (Android 10+, iOS 14+), so a MAC seen
        today may not match the same phone next week. Matching by MAC is a
        bonus signal, not a guarantee - IP matching (against last_seen_ip)
        remains the primary cross-check.
        """
        try:
            with open('/proc/net/arp', 'r') as handle:
                lines = handle.read().splitlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip_str:
                    mac = parts[3].upper()
                    if mac and mac != '00:00:00:00:00:00':
                        return mac
        except Exception as e:
            _logger.debug(f"ARP lookup failed for {ip_str}: {e}")
        return None

    def _get_managed_endpoints(self):
        """Snapshot of IPs and MACs already covered by an agent.

        Used to avoid creating a 'Router / Generic' discovery record for a
        laptop or phone that is already a proper asset.asset or mobile.device
        record with its own agent reporting in. Computed once per scan run,
        not per host, since it needs a full table scan of two models.

        Returns (ip_set, mac_set) - both sets of plain strings, IPs with any
        ':port' suffix stripped (asset.asset.last_seen_ip has been observed
        storing "10.233.35.71:8000").
        """
        ips = set()
        macs = set()

        Asset = self.env['asset.asset'].sudo()
        for field_name in ('ip_address', 'last_seen_ip'):
            if field_name in Asset._fields:
                for value in Asset.search_read(
                        [(field_name, '!=', False)], [field_name]):
                    raw = (value.get(field_name) or '').strip()
                    if raw:
                        ips.add(raw.split(':')[0])
        if 'mac_address' in Asset._fields:
            for value in Asset.search_read(
                    [('mac_address', '!=', False)], ['mac_address']):
                raw = (value.get('mac_address') or '').strip().upper()
                if raw:
                    macs.add(raw)

        # mobile_device_management is a separate, optional module. Check the
        # registry rather than importing it, so this keeps working whether or
        # not that module is installed.
        if 'mobile.device' in self.env:
            Mobile = self.env['mobile.device'].sudo()
            if 'ip_address' in Mobile._fields:
                for value in Mobile.search_read(
                        [('ip_address', '!=', False)], ['ip_address']):
                    raw = (value.get('ip_address') or '').strip()
                    if raw:
                        ips.add(raw.split(':')[0])

        return ips, macs

    # ─── SNMP single GET via subprocess ──────────────────────────────────────
    def _snmp_get(self, ip, oid, community='public', port=161, fast=False):
        """Run snmpget for a single OID. Returns the value string or None.

        - fast=True: short timeout (1s, no retries) — used during discovery to
          quickly decide if a host has SNMP at all. Trades accuracy for speed.
        - fast=False: longer timeout (5s + 1 retry) — used for follow-up queries
          on hosts we've already confirmed have SNMP, where reliability matters.
        """
        if fast:
            snmp_timeout = '1'      # snmpget -t 1
            snmp_retries = '0'      # no retries
            proc_timeout = 3
        else:
            snmp_timeout = '5'
            snmp_retries = '1'
            proc_timeout = 15

        try:
            cmd = ['snmpget', '-Oqv', '-v', '2c', '-c', community,
                   '-t', snmp_timeout, '-r', snmp_retries, f'{ip}:{port}', oid]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=proc_timeout
            )
            if result.returncode == 0:
                value = result.stdout.decode().strip().strip('"')
                return value if value else None
        except subprocess.TimeoutExpired:
            _logger.debug(f"[Discovery] snmpget timed out for {ip}")
        except Exception as e:
            _logger.debug(f"[Discovery] snmpget exception for {ip}: {e}")
        return None

    # ─── Fingerprint device type from sysDescr ───────────────────────────────
    def _fingerprint_device_type(self, sys_descr):
        """Identify device type from SNMP sysDescr string.

        Returns one of: 'router', 'switch', 'firewall', 'access_point'.
        Defaults to 'router' if no specific pattern matches (per project spec
        — placeholder devices and unknown SNMP responses default to router and
        can be reviewed by an admin later).
        """
        if not sys_descr:
            return 'router'
        desc_lower = sys_descr.lower()
        for device_type, keywords in DEVICE_FINGERPRINTS.items():
            for kw in keywords:
                if kw in desc_lower:
                    return device_type
        return 'router'

    def _fingerprint_manufacturer(self, sys_descr):
        """Identify manufacturer from SNMP sysDescr. Returns 'generic' if unknown."""
        if not sys_descr:
            return 'generic'
        desc_lower = sys_descr.lower()
        for manufacturer, keywords in MANUFACTURER_FINGERPRINTS.items():
            for kw in keywords:
                if kw in desc_lower:
                    return manufacturer
        return 'generic'

    # ─── Scan one IP ─────────────────────────────────────────────────────────
    def _scan_ip(self, ip, community, port):
        """Scan a single IP. Returns one of:
          - None: dead host (no ping response) — skip
          - Dict with snmp_reachable=True: host alive AND SNMP responded
          - Dict with snmp_reachable=False: host alive but no SNMP (placeholder)

        Optimization: use a FAST initial SNMP probe (1s timeout) just to decide
        if the host has SNMP. If it does, follow up with full (5s) queries for
        accurate details. This keeps non-SNMP hosts at ~1s instead of ~5s.
        """
        ip_str = str(ip)

        # Fast-path: skip dead hosts entirely. Capture TTL in the same ping
        # rather than pinging twice - it costs nothing extra and is only used
        # later if SNMP turns out to be unavailable.
        alive, ttl = self._ping_with_ttl(ip_str)
        if not alive:
            return None

        # Quick SNMP probe — does this host even support SNMP?
        sys_descr = self._snmp_get(ip_str, OID_SYS_DESCR, community, port, fast=True)
        if sys_descr:
            # SNMP works — do the follow-up queries with normal (5s) timeout.
            # SNMP identifies the device by name/description directly, so the
            # TTL/port heuristics below are not needed on this branch.
            return {
                'ip': ip_str,
                'sys_descr': sys_descr,
                'sys_name': self._snmp_get(ip_str, OID_SYS_NAME, community, port, fast=False),
                'sys_location': self._snmp_get(ip_str, OID_SYS_LOCATION, community, port, fast=False),
                'uptime': self._snmp_get(ip_str, OID_SYS_UPTIME, community, port, fast=False),
                'snmp_reachable': True,
            }

        # Alive but no SNMP → gather the signals that let _sync_single_device
        # guess what this actually is, instead of blanket-labelling it Router.
        mac = self._get_mac_address(ip_str)
        open_ports = self._scan_ports(
            ip_str, [445, 3389, 135, 22, 80, 443])

        return {
            'ip': ip_str,
            'sys_descr': None,
            'sys_name': None,
            'sys_location': None,
            'uptime': None,
            'snmp_reachable': False,
            'ttl': ttl,
            'mac_address': mac,
            'open_ports': open_ports,
        }

    # ─── Auto setup on new database ──────────────────────────────────────────
    @api.model
    def _auto_setup_on_startup(self):
        """Auto-create discovery config and run first scan if none exists.

        Detects the real outbound LAN IP using the UDP-socket trick — this
        avoids the common 127.0.1.1 trap where socket.gethostbyname(hostname)
        returns a localhost alias instead of the real LAN address.
        """
        import socket
        existing = self.search([], limit=1)
        if not existing:
            subnet = '192.168.1.0/24'  # safe default
            try:
                # Open a UDP socket to a public DNS server. No packet is
                # actually sent for UDP connect — Linux just fills in the
                # source IP it WOULD use, which is our real LAN address.
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
                s.close()
                if lan_ip and not lan_ip.startswith("127."):
                    parts = lan_ip.split('.')
                    subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                    _logger.info(
                        "[Discovery] Auto-detected LAN subnet: %s (from IP %s)",
                        subnet, lan_ip
                    )
                else:
                    _logger.warning(
                        "[Discovery] Could not auto-detect LAN IP (got %s), "
                        "using default %s", lan_ip, subnet
                    )
            except Exception as e:
                _logger.warning(
                    "[Discovery] LAN detection failed (%s), using default %s",
                    e, subnet
                )

            existing = self.create({
                'name': 'Auto Network Discovery',
                'subnet': subnet,
                'snmp_community': 'public',
                'snmp_port': 161,
                'auto_create': True,
            })
            _logger.info(f"[Discovery] Auto-created config for subnet: {subnet}")
            existing.run_discovery()

    # ─── Main discovery ──────────────────────────────────────────────────────
    def action_run_discovery_async(self):
        """UI button entry-point. Defers the heavy scan to a one-shot cron job
        so the HTTP request returns immediately and doesn't hit the 120s timeout.

        The user gets a notification 'scan started' and can refresh the page
        in a few minutes to see results.
        """
        self.ensure_one()
        # Create a one-shot cron that runs in the next ~10 seconds and executes
        # the scan in a separate worker process (no HTTP timeout).
        Cron = self.env['ir.cron'].sudo()
        cron = Cron.create({
            'name': f'[Auto] Network Discovery Scan ({self.subnet})',
            'model_id': self.env.ref('asset_management.model_network_discovery_service').id
                        if self.env.ref('asset_management.model_network_discovery_service', raise_if_not_found=False)
                        else self.env['ir.model'].search([('model', '=', 'network.discovery.service')], limit=1).id,
            'state': 'code',
            'code': 'pass',
            'active': True,
            'interval_number': 1,
            'interval_type': 'minutes',
            'nextcall': fields.Datetime.now(),
        })
        # Odoo 17 removed ir.cron.numbercall/doall, so a cron can no longer be
        # declared one-shot. Reproduce the old 'numbercall: 1' behaviour by
        # having the job deactivate itself once it has run.
        cron.code = (
            f"model.browse({self.id}).run_discovery()\n"
            f"env['ir.cron'].browse({cron.id}).write({{'active': False}})"
        )
        _logger.info(f"[Discovery] Async scan scheduled for subnet {self.subnet}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Discovery scan started',
                'message': f'Scanning {self.subnet} in the background. '
                           f'Refresh in 2-3 minutes to see results.',
                'type': 'success',
                'sticky': False,
            }
        }

    def run_discovery(self):
        """Scan subnet, identify live devices, auto-create / auto-update
        asset.network.device records.

        Streams results: each discovered device is saved to DB immediately
        (with a commit every 10 hosts) so partial progress survives even if
        the scan is killed mid-run by a worker timeout.
        """
        config = self if self.id else self.search([], limit=1)
        if not config:
            config = self.create({
                'name': 'Default Discovery Config',
                'subnet': '192.168.1.0/24',
            })

        subnet = config.subnet
        community = config.snmp_community or 'public'
        port = config.snmp_port or 161

        _logger.info(f"[Discovery] Starting scan of {subnet}")

        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError as e:
            _logger.error(f"[Discovery] Invalid subnet {subnet}: {e}")
            return

        # Snapshot which IPs/MACs already belong to an agent-managed asset or
        # mobile device, ONCE for the whole scan. Looked up per-host this
        # would be a full table scan per IP - here it is two queries total.
        managed_ips, managed_macs = self._get_managed_endpoints()
        _logger.info(
            "[Discovery] %s known agent-managed IP(s), %s MAC(s) - these "
            "will not get a duplicate network device record",
            len(managed_ips), len(managed_macs))

        hosts = list(network.hosts())
        total_hosts = len(hosts)
        discovered_count = 0
        commit_every = 10
        processed_since_commit = 0

        # Counters for the summary
        stats = {'confirmed': 0, 'placeholder': 0, 'upgraded': 0, 'updated': 0}

        for i, ip in enumerate(hosts):
            try:
                result = self._scan_ip(ip, community, port)
            except Exception as e:
                _logger.warning(f"[Discovery] Scan error for {ip}: {e}")
                continue

            if not result:
                continue

            discovered_count += 1

            if result.get('snmp_reachable'):
                _logger.info(
                    f"[Discovery] Found via SNMP: {result['ip']} — "
                    f"{result.get('sys_name') or 'Unknown'}"
                )
            else:
                _logger.info(
                    f"[Discovery] Found via ping only: {result['ip']} "
                    f"(no SNMP — will create as placeholder)"
                )

            # Sync this one host to the database immediately
            if config.auto_create:
                try:
                    self._sync_single_device(
                        result, community, port, stats,
                        managed_ips, managed_macs)
                    processed_since_commit += 1
                except Exception as e:
                    _logger.warning(f"[Discovery] Failed to save {result['ip']}: {e}")
                    # Don't let one bad record kill the whole scan
                    self.env.cr.rollback()

            # Commit periodically so progress survives a kill
            if processed_since_commit >= commit_every:
                self.env.cr.commit()
                _logger.info(
                    f"[Discovery] Progress: scanned {i + 1}/{total_hosts}, "
                    f"found {discovered_count} so far "
                    f"(confirmed:{stats['confirmed']} placeholder:{stats['placeholder']} "
                    f"upgraded:{stats['upgraded']} updated:{stats['updated']})"
                )
                processed_since_commit = 0

        # Final commit + summary
        self.env.cr.commit()
        config.write({
            'last_scan': fields.Datetime.now(),
            'discovered_count': discovered_count,
        })
        self.env.cr.commit()

        _logger.info(
            f"[Discovery] Scan COMPLETE for {subnet}. "
            f"{discovered_count} live devices out of {total_hosts} IPs. "
            f"Summary: {stats['confirmed']} new confirmed, "
            f"{stats['placeholder']} new placeholders, "
            f"{stats['upgraded']} upgraded, {stats['updated']} existing updated."
        )

    # ─── Sync ONE discovered host to asset.network.device ────────────────────
    def _sync_single_device(self, item, community, port, stats,
                            managed_ips=None, managed_macs=None):
        """Create or update a single asset.network.device record from one
        discovery result. The stats dict is updated in-place.

        This is called repeatedly during run_discovery() so each successful
        scan result is saved immediately (with periodic commits).

        managed_ips / managed_macs: sets from _get_managed_endpoints(),
        computed once for the whole scan. When the discovered host matches
        one of these, it already belongs to an asset.asset or mobile.device
        with its own agent - this method skips creating a network device
        record for it (or retires a stale placeholder that predates the
        agent enrolling), rather than showing a laptop as a second "Router".
        """
        Device = self.env['asset.network.device']
        Sequence = self.env['ir.sequence']
        managed_ips = managed_ips or set()
        managed_macs = managed_macs or set()

        ip = item['ip']
        sys_descr = item.get('sys_descr') or ''
        sys_name = item.get('sys_name') or ''
        sys_location = item.get('sys_location') or ''
        uptime = item.get('uptime') or ''
        snmp_works = item.get('snmp_reachable', False)
        ttl = item.get('ttl')
        mac = item.get('mac_address')
        open_ports = item.get('open_ports') or []

        existing = Device.search([('ip_address', '=', ip)], limit=1)

        # ── Already agent-managed: this IP/MAC belongs to a laptop or phone
        # that reports through its own agent. Do not create a duplicate.
        is_known_managed = (
            ip in managed_ips or (mac and mac in managed_macs))
        if is_known_managed:
            if existing and (existing.discovery_state or '') == 'placeholder':
                # This placeholder was created before the agent enrolled (or
                # before this cross-check existed). Retire it rather than
                # leave a stale duplicate sitting in Network Devices.
                existing.write({'active': False})
                existing.message_post(body=(
                    "Deactivated: this IP/MAC now matches an agent-managed "
                    "asset or mobile device."))
                stats['retired_managed'] = stats.get('retired_managed', 0) + 1
                _logger.info(
                    "[Discovery] Retired placeholder %s - now agent-managed", ip)
            else:
                stats['skipped_managed'] = stats.get('skipped_managed', 0) + 1
                _logger.debug(
                    "[Discovery] Skipped %s - already agent-managed", ip)
            return

        if snmp_works:
            device_type = self._fingerprint_device_type(sys_descr)
            manufacturer = self._fingerprint_manufacturer(sys_descr)
            os_guess = False
            is_unmanaged_endpoint = False
            new_state = 'snmp_confirmed'
        else:
            # No SNMP reply and no match against a known agent. Guess from
            # TTL + a small signature port probe instead of blanket-labelling
            # this Router, which is what made every unidentified host on the
            # network look identical.
            ttl_guess = self._guess_os_from_ttl(ttl)
            has_windows_port = bool(set(open_ports) & {445, 3389, 135})
            has_unix_port = 22 in open_ports
            has_web_port = bool(set(open_ports) & {80, 443})

            if ttl_guess == 'windows' or has_windows_port:
                device_type, os_guess = 'endpoint', 'windows'
                is_unmanaged_endpoint = True
            elif ttl_guess == 'linux_mac_mobile':
                if has_web_port and not has_unix_port:
                    # A device with a web admin UI and no SSH, TTL~64, is more
                    # likely a Linux-based router/AP than a workstation - many
                    # consumer routers run embedded Linux with this TTL too.
                    device_type, os_guess = 'router', 'network_device'
                    is_unmanaged_endpoint = False
                else:
                    device_type = 'endpoint'
                    os_guess = 'linux_mac' if has_unix_port else 'mobile_or_unknown'
                    is_unmanaged_endpoint = True
            elif ttl_guess == 'network_device':
                device_type, os_guess = 'router', 'network_device'
                is_unmanaged_endpoint = False
            else:
                # No TTL captured at all (ping subprocess unavailable/failed
                # to parse) - be honest that nothing was actually learned,
                # rather than defaulting to Router as if that were a finding.
                device_type, os_guess = 'unknown', 'unknown'
                is_unmanaged_endpoint = False

            manufacturer = 'generic'
            new_state = 'placeholder'

        discovery_vals_extra = {
            'mac_address': mac or False,
            'ttl_observed': ttl or 0,
            'os_guess': os_guess,
            'open_ports': ','.join(str(p) for p in open_ports) if open_ports else False,
            'is_unmanaged_endpoint': is_unmanaged_endpoint,
        } if not snmp_works else {}

        if existing:
            vals = {
                'connection_status': 'online' if snmp_works else 'unreachable',
                'last_check': fields.Datetime.now(),
            }
            if snmp_works:
                vals['last_online'] = fields.Datetime.now()
            else:
                # Refresh the heuristic signals every scan - a ping-only host
                # can change device_type/os_guess as new ports open, or once
                # a fresh MAC is visible in the ARP table.
                vals.update(discovery_vals_extra)
                if device_type != existing.device_type:
                    vals['device_type'] = device_type

            # Dynamic device-type upgrade: existing record was a placeholder,
            # SNMP now responds → update with the real device type/manufacturer.
            existing_state = getattr(existing, 'discovery_state', False) or ''

            if existing_state == 'placeholder' and snmp_works:
                vals.update({
                    'device_type': device_type,
                    'manufacturer': manufacturer,
                    'notes': sys_descr,
                    # A confirmed real device answering SNMP is not an
                    # unidentified endpoint, regardless of the earlier guess.
                    'is_unmanaged_endpoint': False,
                })
                if uptime:
                    vals['uptime'] = uptime
                if sys_name and (not existing.name or existing.name.startswith('Device-')):
                    vals['name'] = sys_name
                if sys_location and (not existing.location or existing.location == 'Auto-discovered'):
                    vals['location'] = sys_location
                if 'discovery_state' in existing._fields:
                    vals['discovery_state'] = 'snmp_confirmed'
                stats['upgraded'] += 1
                _logger.info(
                    "[Discovery] UPGRADED placeholder %s → %s (SNMP now responding, %s)",
                    ip, device_type, manufacturer
                )
            else:
                if snmp_works:
                    if sys_descr:
                        vals['notes'] = sys_descr
                    if uptime:
                        vals['uptime'] = uptime
                stats['updated'] += 1

            existing.write(vals)
        else:
            # Create new record
            if snmp_works:
                name = sys_name or f"Device-{ip.replace('.', '-')}"
            elif device_type == 'endpoint':
                # Distinguish these in the list/kanban from real placeholders -
                # "Device-x-x-x-x" reads identically for a router and a
                # laptop, which was the original complaint.
                name = f"Unidentified Endpoint-{ip.replace('.', '-')}"
            else:
                name = f"Device-{ip.replace('.', '-')}"
            code = Sequence.next_by_code('asset.asset.network') or f"NET-{ip.replace('.', '')}"

            create_vals = {
                'name': name,
                'device_code': code,
                'device_type': device_type,
                'manufacturer': manufacturer,
                'ip_address': ip,
                'location': sys_location or 'Auto-discovered',
                'snmp_community': community,
                'snmp_port': port,
                'snmp_version': 'v2c',
                'connection_status': 'online' if snmp_works else 'unreachable',
                'last_check': fields.Datetime.now(),
                'last_online': fields.Datetime.now() if snmp_works else False,
                'uptime': uptime,
                'notes': sys_descr,
                'is_active': True,
            }
            create_vals.update(discovery_vals_extra)
            if 'discovery_state' in Device._fields:
                create_vals['discovery_state'] = new_state

            Device.create(create_vals)

            if snmp_works:
                stats['confirmed'] += 1
                _logger.info(
                    "[Discovery] Created CONFIRMED %s: %s (%s) [%s]",
                    device_type, name, ip, manufacturer
                )
            elif device_type == 'endpoint':
                stats['unmanaged_endpoint'] = stats.get('unmanaged_endpoint', 0) + 1
                _logger.info(
                    "[Discovery] Created UNMANAGED ENDPOINT guess (%s) for %s "
                    "- no agent found for this IP/MAC. Investigate.",
                    os_guess, ip
                )
            else:
                stats['placeholder'] += 1
                _logger.info(
                    "[Discovery] Created PLACEHOLDER router for %s "
                    "(no SNMP — enable SNMP on the device and re-scan to identify)",
                    ip
                )

    # ─── Legacy batch sync (kept for backward compatibility) ─────────────────
    def _sync_devices(self, discovered, community, port):
        """Batch sync — kept for any external callers. New code should use
        the streaming run_discovery() which calls _sync_single_device per host."""
        stats = {'confirmed': 0, 'placeholder': 0, 'upgraded': 0, 'updated': 0}
        managed_ips, managed_macs = self._get_managed_endpoints()
        for item in discovered:
            try:
                self._sync_single_device(
                    item, community, port, stats, managed_ips, managed_macs)
            except Exception as e:
                _logger.warning(f"[Discovery] Sync error for {item.get('ip')}: {e}")
        _logger.info(
            "[Discovery] Summary: %d new confirmed, %d new placeholders, "
            "%d upgraded (placeholder→confirmed), %d existing updated",
            stats['confirmed'], stats['placeholder'], stats['upgraded'], stats['updated']
        )