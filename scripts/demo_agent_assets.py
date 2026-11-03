#!/usr/bin/env python3
"""Sample Windows / macOS assets, created the way a real agent creates them.

Posts the same static payload the Windows / macOS agents send to
/api/laptop_monitor (hardware, OS, installed apps), then a live heartbeat
to /api/laptop_monitor/live. Re-running updates the same serials.

    python3 scripts/demo_agent_assets.py http://192.168.105.115:8075 asset_crm
"""
import json
import random
import sys
import urllib.request

URL, DB = sys.argv[1].rstrip('/'), sys.argv[2]
random.seed(115)

WIN_APPS = [
    ('Google Chrome', 'Google LLC', '130.0.6723.117', 87200),
    ('Microsoft Edge', 'Microsoft Corporation', '130.0.2849.80', 0),
    ('Microsoft 365 Apps for enterprise - en-us', 'Microsoft Corporation', '16.0.18129.20116', 0),
    ('Microsoft Teams', 'Microsoft Corporation', '24295.605.3225.8804', 0),
    ('Zoom Workplace (64-bit)', 'Zoom Video Communications, Inc.', '6.2.5.50870', 384000),
    ('7-Zip 24.08 (x64)', 'Igor Pavlov', '24.08', 5900),
    ('Adobe Acrobat Reader (64-bit)', 'Adobe', '24.004.20219', 712000),
    ('Notepad++ (64-bit x64)', 'Notepad++ Team', '8.7', 15000),
    ('AnyDesk', 'AnyDesk Software GmbH', '8.1.0', 6200),
    ('Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.40.33810', 'Microsoft Corporation', '14.40.33810.0', 20400),
    ('Quick Heal Total Security', 'Quick Heal Technologies Ltd.', '24.00', 960000),
    ('Tally.ERP 9', 'Tally Solutions Pvt. Ltd.', '6.6.3', 210000),
    ('Python 3.12.7 (64-bit)', 'Python Software Foundation', '3.12.7150.0', 104000),
    ('Visual Studio Code', 'Microsoft Corporation', '1.95.1', 382000),
]
MAC_APPS = [
    ('Google Chrome', 'Google LLC', '130.0.6723.117', 1200000),
    ('Microsoft Word', 'Microsoft Corporation', '16.90', 2400000),
    ('Microsoft Excel', 'Microsoft Corporation', '16.90', 2100000),
    ('Microsoft Teams', 'Microsoft Corporation', '24295.606.3238.6194', 1100000),
    ('Slack', 'Slack Technologies, LLC', '4.41.97', 520000),
    ('zoom.us', 'Zoom Video Communications, Inc.', '6.2.6', 440000),
    ('Visual Studio Code', 'Microsoft Corporation', '1.95.1', 610000),
    ('Xcode', 'Apple Inc.', '16.1', 12000000),
    ('Keynote', 'Apple Inc.', '14.2', 690000),
    ('AnyDesk', 'philandro Software GmbH', '8.1.1', 60000),
]

# serial, hostname, model, os_name, version, codename, cpu, ram, disk, chassis, user
WINDOWS = [
    ('5CG2471K9P', 'FNT-LT-ACC01', 'HP EliteBook 840 G9', 'Windows 11', '10.0.22631', '23H2', '12th Gen Intel(R) Core(TM) i5-1245U', 16, 512, 'Notebook'),
    ('5CG3302QHX', 'FNT-LT-HR02', 'HP ProBook 450 G10', 'Windows 11', '10.0.22631', '23H2', '13th Gen Intel(R) Core(TM) i5-1335U', 16, 512, 'Notebook'),
    ('PF4K8ZLM', 'FNT-LT-DEV03', 'Lenovo ThinkPad E14 Gen 5', 'Windows 11', '10.0.26100', '24H2', '13th Gen Intel(R) Core(TM) i7-1355U', 32, 1024, 'Notebook'),
    ('PF3WQ7A1', 'FNT-LT-SAL04', 'Lenovo ThinkPad L14 Gen 3', 'Windows 10', '10.0.19045', '22H2', '12th Gen Intel(R) Core(TM) i5-1235U', 8, 256, 'Notebook'),
    ('8J4XKQ3', 'FNT-LT-OPS05', 'Dell Latitude 5440', 'Windows 11', '10.0.22631', '23H2', '13th Gen Intel(R) Core(TM) i5-1345U', 16, 512, 'Laptop'),
    ('3HZ9VR2', 'FNT-LT-FIN06', 'Dell Latitude 3420', 'Windows 10', '10.0.19045', '22H2', '11th Gen Intel(R) Core(TM) i5-1135G7', 8, 512, 'Laptop'),
    ('BX7M2T3', 'FNT-DT-RCP07', 'Dell OptiPlex 7010', 'Windows 11', '10.0.22631', '23H2', '13th Gen Intel(R) Core(TM) i5-13500', 16, 512, 'Desktop'),
    ('MP2B4KQL', 'FNT-DT-ACC08', 'Lenovo ThinkCentre M70s Gen 4', 'Windows 11', '10.0.22631', '23H2', '13th Gen Intel(R) Core(TM) i5-13400', 16, 512, 'Desktop'),
    ('NXKN3SI00J3251A6E77600', 'FNT-LT-MKT09', 'Acer TravelMate P214-54', 'Windows 10', '10.0.19045', '22H2', '12th Gen Intel(R) Core(TM) i5-1235U', 8, 512, 'Notebook'),
    ('R9NRKD012345678', 'FNT-LT-ADM10', 'ASUS ExpertBook B1 B1402CVA', 'Windows 11', '10.0.26100', '24H2', '13th Gen Intel(R) Core(TM) i5-1335U', 16, 512, 'Notebook'),
]
MACS = [
    ('C02FK3XHMD6M', 'FNT-MBP-DES01', 'MacBookPro18,3', 'macOS Sequoia', '15.1', 'Sequoia', 'Apple M1 Pro', 16, 512, 'Laptop'),
    ('FVFH4P2RQ6L4', 'FNT-MBA-CEO02', 'Mac14,2', 'macOS Sequoia', '15.1', 'Sequoia', 'Apple M2', 16, 512, 'Laptop'),
    ('K7XW9J2L4V', 'FNT-MBP-DEV03', 'Mac15,6', 'macOS Sequoia', '15.0.1', 'Sequoia', 'Apple M3 Pro', 18, 512, 'Laptop'),
    ('C02DT4ZKQ05N', 'FNT-MBA-SAL04', 'MacBookAir10,1', 'macOS Sonoma', '14.7.1', 'Sonoma', 'Apple M1', 8, 256, 'Laptop'),
    ('H4TFQ9L2MN', 'FNT-IMAC-MKT05', 'iMac24,1', 'macOS Sonoma', '14.7', 'Sonoma', 'Apple M3', 16, 512, 'Desktop'),
]


def post(path, payload):
    req = urllib.request.Request(
        URL + path, json.dumps(payload).encode(),
        {'Content-Type': 'application/json', 'X-Odoo-Database': DB})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()[:120]


def mac_addr():
    return ':'.join('%02X' % random.randint(0, 255) for _ in range(6))


def send(rows, plat, os_type, apps):
    for i, (sn, host, model, os_name, ver, code, cpu, ram, disk, chassis) in enumerate(rows):
        ip = '192.168.105.%d' % (140 + i + (20 if plat == 'darwin' else 0))
        picked = random.sample(apps, k=min(len(apps), random.randint(7, len(apps))))
        payload = {
            'serial_number': sn, 'hostname': host, 'device_name': model,
            'platform': plat, 'os_type': os_type, 'os_name': os_name,
            'os_version': ver, 'os_codename': code, 'processor': cpu,
            'ram_size': ram, 'rom_size': disk, 'mac_address': mac_addr(),
            'ip_address': ip, 'local_ip': ip, 'chassis_type': chassis,
            'location_source': 'unavailable',
            'installed_apps': json.dumps([
                {'name': n, 'publisher': p, 'version': v, 'size': s,
                 'installed_date': '2024%02d%02d' % (random.randint(1, 12), random.randint(1, 28))}
                for n, p, v, s in picked]),
        }
        print(sn, host, post('/api/laptop_monitor', payload))
        post('/api/laptop_monitor/live', {
            'serial_number': sn, 'local_ip': ip,
            'cpu_usage_percent': round(random.uniform(4, 45), 1),
            'ram_usage_percent': round(random.uniform(35, 82), 1),
            'disk_usage_percent': round(random.uniform(30, 78), 1),
            'network_upload_mbps': round(random.uniform(0.1, 3), 2),
            'network_download_mbps': round(random.uniform(0.5, 25), 2),
            'battery_percentage': 100 if chassis == 'Desktop' else random.randint(35, 98),
        })


send(WINDOWS, 'windows', 'Windows', WIN_APPS)
send(MACS, 'darwin', 'macOS', MAC_APPS)
