# -*- coding: utf-8 -*-
"""
App Deployment Wizard Model

Transient model for the "Deploy Application" modal dialog.
Provides preset application commands and device selection.
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
from urllib.parse import urlparse
import logging

_logger = logging.getLogger(__name__)


class AppDeploymentWizard(models.TransientModel):
    """
    Deploy Application Wizard

    Provides a user-friendly modal for creating app deployment tasks
    with preset application commands and device selection.
    """
    _name = 'asset_management.app_deployment_wizard'
    _description = 'Deploy Application Wizard'

    # =========================================================================
    # PRESET APPLICATION COMMANDS
    # =========================================================================
    PRESET_COMMANDS = {
        'chrome': {
            'winget': 'winget install Google.Chrome',
            'chocolatey': 'choco install googlechrome -y',
            'homebrew': 'brew install --cask google-chrome',
            'apt': 'wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - && apt install google-chrome-stable'
        },
        'firefox': {
            'winget': 'winget install Mozilla.Firefox',
            'chocolatey': 'choco install firefox -y',
            'homebrew': 'brew install --cask firefox',
            'apt': 'apt install firefox'
        },
        'vscode': {
            'winget': 'winget install Microsoft.VisualStudioCode',
            'chocolatey': 'choco install vscode -y',
            'homebrew': 'brew install --cask visual-studio-code',
            'apt': 'wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > packages.microsoft.gpg && install -D -o root -g root -m 644 packages.microsoft.gpg /etc/apt/keyrings/packages.microsoft.gpg && sh -c \'echo "deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" > /etc/apt/sources.list.d/vscode.list\' && rm -f packages.microsoft.gpg && apt update && apt install code'
        },
        'zoom': {
            'winget': 'winget install Zoom.Zoom',
            'chocolatey': 'choco install zoom -y',
            'homebrew': 'brew install --cask zoom',
            'apt': 'wget https://zoom.us/client/latest/zoom_amd64.deb && apt install ./zoom_amd64.deb'
        },
        'slack': {
            'winget': 'winget install SlackTechnologies.Slack',
            'chocolatey': 'choco install slack -y',
            'homebrew': 'brew install --cask slack',
            'apt': 'wget -qO- https://packagecloud.io/slacktechnologies/slack/gpgkey | apt-key add - && apt install slack-desktop'
        },
        'teams': {
            'winget': 'winget install Microsoft.Teams',
            'chocolatey': 'choco install microsoft-teams -y',
            'homebrew': 'brew install --cask microsoft-teams',
            'apt': 'apt install teams'
        },
        '7zip': {
            'winget': 'winget install 7zip.7zip',
            'chocolatey': 'choco install 7zip -y',
            'homebrew': 'brew install --cask keka',
            'apt': 'apt install p7zip-full'
        },
        'notepadpp': {
            'winget': 'winget install Notepad++.Notepad++',
            'chocolatey': 'choco install notepadplusplus -y',
            'homebrew': 'brew install --cask visual-studio-code',
            'apt': 'apt install gedit'
        },
        'vlc': {
            'winget': 'winget install VideoLAN.VLC',
            'chocolatey': 'choco install vlc -y',
            'homebrew': 'brew install --cask vlc',
            'apt': 'apt install vlc'
        },
    }

    PRESET_LABELS = {
        'chrome': 'Google Chrome',
        'firefox': 'Mozilla Firefox',
        'vscode': 'Visual Studio Code',
        'zoom': 'Zoom',
        'slack': 'Slack',
        'teams': 'Microsoft Teams',
        '7zip': '7-Zip',
        'notepadpp': 'Notepad++',
        'vlc': 'VLC Media Player',
        'custom': 'Custom...'
    }

    UNINSTALL_COMMANDS = {
        'chrome': {
            'winget': 'winget uninstall Google.Chrome',
            'chocolatey': 'choco uninstall googlechrome -y',
            'homebrew': 'brew uninstall --cask google-chrome',
            'apt': 'apt remove google-chrome-stable -y',
        },
        'firefox': {
            'winget': 'winget uninstall Mozilla.Firefox',
            'chocolatey': 'choco uninstall firefox -y',
            'homebrew': 'brew uninstall --cask firefox',
            'apt': 'apt remove firefox -y',
        },
        'vscode': {
            'winget': 'winget uninstall Microsoft.VisualStudioCode',
            'chocolatey': 'choco uninstall vscode -y',
            'homebrew': 'brew uninstall --cask visual-studio-code',
            'apt': 'apt remove code -y',
        },
        'zoom': {
            'winget': 'winget uninstall Zoom.Zoom',
            'chocolatey': 'choco uninstall zoom -y',
            'homebrew': 'brew uninstall --cask zoom',
            'apt': 'apt remove zoom -y',
        },
        'slack': {
            'winget': 'winget uninstall SlackTechnologies.Slack',
            'chocolatey': 'choco uninstall slack -y',
            'homebrew': 'brew uninstall --cask slack',
            'apt': 'apt remove slack-desktop -y',
        },
        'teams': {
            'winget': 'winget uninstall Microsoft.Teams',
            'chocolatey': 'choco uninstall microsoft-teams -y',
            'homebrew': 'brew uninstall --cask microsoft-teams',
            'apt': 'apt remove teams -y',
        },
        '7zip': {
            'winget': 'winget uninstall 7zip.7zip',
            'chocolatey': 'choco uninstall 7zip -y',
            'homebrew': 'brew uninstall --cask keka',
            'apt': 'apt remove p7zip-full -y',
        },
        'notepadpp': {
            'winget': 'winget uninstall Notepad++.Notepad++',
            'chocolatey': 'choco uninstall notepadplusplus -y',
            'homebrew': 'brew uninstall --cask visual-studio-code',
            'apt': 'apt remove gedit -y',
        },
        'vlc': {
            'winget': 'winget uninstall VideoLAN.VLC',
            'chocolatey': 'choco uninstall vlc -y',
            'homebrew': 'brew uninstall --cask vlc',
            'apt': 'apt remove vlc -y',
        },
    }

    # =========================================================================
    # DEVICE SELECTION
    # =========================================================================
    device_id = fields.Many2one(
        'asset.asset',
        string='Target Device',
        required=False,
        domain="[('os_platform', '=', platform_filter)]",
        help='Select the target device for deployment'
    )

    # =========================================================================
    # DEVICE NAME DISPLAY (read-only)
    # =========================================================================
    device_name_display = fields.Char(
        string='Device',
        compute='_compute_device_name_display',
        help='Selected device name'
    )

    # =========================================================================
    # PLATFORM FILTER (from context)
    # =========================================================================
    platform_filter = fields.Selection([
        ('windows', 'Windows'),
        ('linux', 'Linux'),
        ('macos', 'macOS'),
    ], string='Platform Filter', default=lambda self: self.env.context.get('default_platform'))

    # =========================================================================
    # ACTION TYPE (install / uninstall)
    # =========================================================================
    action_type = fields.Selection([
        ('install', 'Install'),
        ('uninstall', 'Uninstall'),
    ], string='Action', required=True,
       default=lambda self: self.env.context.get('default_action_type', 'install'))

    # =========================================================================
    # INSTALLATION SOURCE  (package manager preset  OR  direct URL download)
    # =========================================================================
    application_source = fields.Selection([
        ('preset', 'From Package Manager'),
        ('url',    'From URL Installer'),
    ], string='Installation Method', required=True, default='preset')

    # ── URL Installer fields ──────────────────────────────────────────────────
    installer_url = fields.Char(
        string='Installer URL',
        help='Direct download URL to the installer file (e.g., a GitHub Releases asset URL)'
    )
    # Single field (all options) — used as the resolved value written to the record
    installer_type = fields.Selection([
        ('exe',      'Windows Executable (.exe)'),
        ('msi',      'Windows Installer (.msi)'),
        ('deb',      'Debian Package (.deb)'),
        ('rpm',      'RPM Package (.rpm)'),
        ('pkg',      'macOS Package (.pkg)'),
        ('dmg',      'macOS Disk Image (.dmg)'),
        ('appimage', 'Linux AppImage'),
        ('zip',      'ZIP Archive'),
    ], string='Installer Type', default='exe')

    # Platform-specific fields — only the relevant options shown in the wizard
    installer_type_windows = fields.Selection([
        ('exe', 'Windows Executable (.exe)'),
        ('msi', 'Windows Installer (.msi)'),
        ('zip', 'ZIP Archive'),
    ], string='Installer Type', default='exe')

    installer_type_linux = fields.Selection([
        ('deb',      'Debian Package (.deb)'),
        ('rpm',      'RPM Package (.rpm)'),
        ('appimage', 'Linux AppImage'),
        ('zip',      'ZIP Archive'),
    ], string='Installer Type', default='deb')

    installer_type_macos = fields.Selection([
        ('pkg', 'macOS Package (.pkg)'),
        ('dmg', 'macOS Disk Image (.dmg)'),
        ('zip', 'ZIP Archive'),
    ], string='Installer Type', default='pkg')

    # =========================================================================
    # APPLICATION SELECTION
    # =========================================================================
    application_preset = fields.Selection([
        ('chrome', 'Google Chrome'),
        ('firefox', 'Mozilla Firefox'),
        ('vscode', 'Visual Studio Code'),
        ('zoom', 'Zoom'),
        ('slack', 'Slack'),
        ('teams', 'Microsoft Teams'),
        ('7zip', '7-Zip'),
        ('notepadpp', 'Notepad++'),
        ('vlc', 'VLC Media Player'),
        ('custom', 'Custom...')
    ], string='Application', required=True, default='chrome')

    application_name = fields.Char(
        string='Application Name',
        help='Custom application name (only shown if Custom is selected)'
    )

    # =========================================================================
    # PACKAGE MANAGER & COMMAND
    # =========================================================================
    # Generic field (all options) – used when no platform filter is active and
    # as the final value written to the deployment record.
    package_manager = fields.Selection([
        ('winget', 'winget (Windows)'),
        ('chocolatey', 'Chocolatey (Windows)'),
        ('homebrew', 'Homebrew (macOS)'),
        ('apt', 'apt (Linux)'),
        ('custom', 'Custom Command'),
    ], string='Package Manager', required=False)

    # Platform-specific fields – each exposes only the options that make sense
    # for that OS, so the dropdown is filtered on each platform's deployment page.
    package_manager_windows = fields.Selection([
        ('winget', 'winget (Windows)'),
        ('chocolatey', 'Chocolatey (Windows)'),
        ('custom', 'Custom Command'),
    ], string='Package Manager', default='winget')

    package_manager_linux = fields.Selection([
        ('apt', 'apt (Linux)'),
        ('custom', 'Custom Command'),
    ], string='Package Manager', default='apt')

    package_manager_macos = fields.Selection([
        ('homebrew', 'Homebrew (macOS)'),
        ('custom', 'Custom Command'),
    ], string='Package Manager', default='homebrew')

    install_command = fields.Text(
        string='Install Command',
        required=True,
        help='Full installation command'
    )

    # =========================================================================
    # NOTES
    # =========================================================================
    notes = fields.Text(
        string='Notes (optional)',
        help='Reason for deployment or additional notes'
    )

    # =========================================================================
    # DEVICE INFO (for display)
    # =========================================================================
    device_platform = fields.Selection(
        string='Platform',
        related='device_id.os_platform',
        readonly=True
    )
    device_online = fields.Boolean(
        string='Online',
        compute='_compute_device_online',
        help='Indicates if device is currently online'
    )

    # =========================================================================
    # COMPUTE METHODS
    # =========================================================================
    @api.depends('device_id')
    def _compute_device_name_display(self):
        """Compute device name display"""
        for wizard in self:
            wizard.device_name_display = wizard.device_id.asset_name or wizard.device_id.asset_code if wizard.device_id else ''

    @api.depends('device_id', 'device_id.last_sync_time')
    def _compute_device_online(self):
        """Check if device is online (synced within last 5 minutes)"""
        for wizard in self:
            if wizard.device_id and wizard.device_id.last_sync_time:
                time_diff = datetime.now() - wizard.device_id.last_sync_time
                wizard.device_online = time_diff.total_seconds() < 300
            else:
                wizard.device_online = False

    # =========================================================================
    # ONCHANGE METHODS
    # =========================================================================
    def _get_effective_platform(self):
        """Return the active platform: device OS > platform_filter field > context fallback"""
        if self.device_id:
            return self.device_id.os_platform or ''
        return self.platform_filter or self.env.context.get('default_platform') or ''

    def _set_default_package_manager(self, platform):
        """Initialise the right platform-specific field and sync the generic field"""
        if platform == 'windows':
            self.package_manager_windows = self.package_manager_windows or 'winget'
            self.package_manager = self.package_manager_windows
        elif platform == 'linux':
            self.package_manager_linux = self.package_manager_linux or 'apt'
            self.package_manager = self.package_manager_linux
        elif platform == 'macos':
            self.package_manager_macos = self.package_manager_macos or 'homebrew'
            self.package_manager = self.package_manager_macos
        else:
            self.package_manager = self.package_manager or 'winget'

    # =========================================================================
    # URL INSTALLER — command generator
    # =========================================================================
    def _get_effective_installer_type(self):
        """Return the installer type from the correct platform-specific field."""
        platform = self._get_effective_platform()
        if platform == 'windows':
            return self.installer_type_windows or self.installer_type or 'exe'
        elif platform == 'linux':
            return self.installer_type_linux or self.installer_type or 'deb'
        elif platform == 'macos':
            return self.installer_type_macos or self.installer_type or 'pkg'
        # No platform filter — fall back to the generic field
        return self.installer_type or 'exe'

    def _sync_installer_type(self):
        """Sync the effective platform-specific type → generic installer_type field."""
        platform = self._get_effective_platform()
        if platform == 'windows':
            self.installer_type = self.installer_type_windows or 'exe'
        elif platform == 'linux':
            self.installer_type = self.installer_type_linux or 'deb'
        elif platform == 'macos':
            self.installer_type = self.installer_type_macos or 'pkg'

    def _get_installer_filename(self):
        """Extract filename from installer_url, falling back to type-based default."""
        url = self.installer_url or ''
        itype = self._get_effective_installer_type()
        ext_map = {
            'exe': 'installer.exe', 'msi': 'installer.msi',
            'deb': 'package.deb',   'rpm': 'package.rpm',
            'pkg': 'installer.pkg', 'dmg': 'installer.dmg',
            'appimage': 'app.AppImage', 'zip': 'archive.zip',
        }
        if url:
            path = urlparse(url).path
            fname = path.rstrip('/').split('/')[-1]
            if fname and '.' in fname:
                return fname
        return ext_map.get(itype, 'installer')

    def _generate_url_install_command(self):
        """Build a platform-appropriate silent download-and-install shell command."""
        url = self.installer_url or ''
        if not url:
            return ''
        itype = self._get_effective_installer_type()
        fname = self._get_installer_filename()

        if itype == 'exe':
            # /S  — standard NSIS silent flag (most common EXE installer framework)
            return (
                f"powershell -NoProfile -NonInteractive -Command \"& {{ "
                f"$f = Join-Path $env:TEMP '{fname}'; "
                f"Invoke-WebRequest -Uri '{url}' -OutFile $f -UseBasicParsing; "
                f"Start-Process $f -ArgumentList '/S' -Wait -NoNewWindow; "
                f"Remove-Item $f -ErrorAction SilentlyContinue }}\""
            )
        elif itype == 'msi':
            # /quiet /norestart — standard MSI silent flags
            return (
                f"powershell -NoProfile -NonInteractive -Command \"& {{ "
                f"$f = Join-Path $env:TEMP '{fname}'; "
                f"Invoke-WebRequest -Uri '{url}' -OutFile $f -UseBasicParsing; "
                f"Start-Process msiexec.exe -ArgumentList '/i', $f, '/quiet', '/norestart' -Wait -NoNewWindow; "
                f"Remove-Item $f -ErrorAction SilentlyContinue }}\""
            )
        elif itype == 'deb':
            return (
                f"wget -q -O /tmp/{fname} '{url}' && "
                f"dpkg -i /tmp/{fname} && "
                f"rm -f /tmp/{fname}"
            )
        elif itype == 'rpm':
            return (
                f"wget -q -O /tmp/{fname} '{url}' && "
                f"rpm -ivh /tmp/{fname} && "
                f"rm -f /tmp/{fname}"
            )
        elif itype == 'pkg':
            return (
                f"curl -fsSL -o /tmp/{fname} '{url}' && "
                f"installer -pkg /tmp/{fname} -target / && "
                f"rm -f /tmp/{fname}"
            )
        elif itype == 'dmg':
            return (
                f"curl -fsSL -o /tmp/{fname} '{url}' && "
                f"MOUNT=$(hdiutil attach /tmp/{fname} -nobrowse -quiet | tail -1 | awk '{{print $NF}}') && "
                f"cp -R \"$MOUNT\"/*.app /Applications/ && "
                f"hdiutil detach \"$MOUNT\" -quiet && "
                f"rm -f /tmp/{fname}"
            )
        elif itype == 'appimage':
            return (
                f"wget -q -O /tmp/{fname} '{url}' && "
                f"chmod +x /tmp/{fname}"
            )
        elif itype == 'zip':
            dest = '/tmp/' + fname.replace('.zip', '').replace('.ZIP', '')
            return (
                f"wget -q -O /tmp/{fname} '{url}' && "
                f"unzip /tmp/{fname} -d {dest} && "
                f"rm -f /tmp/{fname}"
            )
        return ''

    def _auto_detect_installer_type(self):
        """Guess installer_type from the URL extension."""
        url = self.installer_url or ''
        path = urlparse(url).path.lower()
        for ext, itype in [
            ('.exe', 'exe'), ('.msi', 'msi'),
            ('.deb', 'deb'), ('.rpm', 'rpm'),
            ('.pkg', 'pkg'), ('.dmg', 'dmg'),
            ('.appimage', 'appimage'), ('.zip', 'zip'),
        ]:
            if path.endswith(ext):
                return itype
        return 'exe'

    @api.onchange('installer_url')
    def _onchange_installer_url(self):
        """Auto-detect type from URL into the platform-specific field, then regenerate command."""
        if self.installer_url and self.application_source == 'url':
            detected = self._auto_detect_installer_type()
            if detected:
                # Set whichever platform-specific field is visible
                platform = self._get_effective_platform()
                if platform == 'windows':
                    if detected in ('exe', 'msi', 'zip'):
                        self.installer_type_windows = detected
                elif platform == 'linux':
                    if detected in ('deb', 'rpm', 'appimage', 'zip'):
                        self.installer_type_linux = detected
                elif platform == 'macos':
                    if detected in ('pkg', 'dmg', 'zip'):
                        self.installer_type_macos = detected
                else:
                    self.installer_type = detected
                self._sync_installer_type()
            if self.action_type == 'install':
                self.install_command = self._generate_url_install_command()

    @api.onchange('installer_type_windows')
    def _onchange_installer_type_windows(self):
        """Sync Windows-specific type → generic field and regenerate command."""
        self.installer_type = self.installer_type_windows or 'exe'
        if self.application_source == 'url' and self.action_type == 'install':
            self.install_command = self._generate_url_install_command()

    @api.onchange('installer_type_linux')
    def _onchange_installer_type_linux(self):
        """Sync Linux-specific type → generic field and regenerate command."""
        self.installer_type = self.installer_type_linux or 'deb'
        if self.application_source == 'url' and self.action_type == 'install':
            self.install_command = self._generate_url_install_command()

    @api.onchange('installer_type_macos')
    def _onchange_installer_type_macos(self):
        """Sync macOS-specific type → generic field and regenerate command."""
        self.installer_type = self.installer_type_macos or 'pkg'
        if self.application_source == 'url' and self.action_type == 'install':
            self.install_command = self._generate_url_install_command()

    @api.onchange('application_source')
    def _onchange_application_source(self):
        """Switch between preset and URL modes — clear irrelevant fields."""
        if self.application_source == 'url':
            self.application_preset = False
            self.install_command = self._generate_url_install_command()
        else:
            self.installer_url = False
            self.installer_type = 'exe'
            self.installer_type_windows = 'exe'
            self.installer_type_linux = 'deb'
            self.installer_type_macos = 'pkg'
            self._refresh_install_command()

    # =========================================================================
    # PRESET — command refresh
    # =========================================================================
    def _refresh_install_command(self):
        """Re-generate the command from the current preset + package manager + action_type"""
        if self.application_source == 'url':
            if self.action_type == 'install':
                self.install_command = self._generate_url_install_command()
            return
        if self.application_preset and self.application_preset != 'custom' and self.package_manager:
            if self.action_type == 'uninstall':
                preset_commands = self.UNINSTALL_COMMANDS.get(self.application_preset, {})
            else:
                preset_commands = self.PRESET_COMMANDS.get(self.application_preset, {})
            self.install_command = preset_commands.get(self.package_manager, '')

    @api.onchange('application_preset', 'device_id')
    def _onchange_application_preset(self):
        """Auto-populate application name, package manager, and install command"""
        if not self.application_preset:
            return

        # Set application name
        if self.application_preset == 'custom':
            self.application_name = ''
        else:
            self.application_name = self.PRESET_LABELS.get(self.application_preset, '')

        # Pick the right package manager for the active platform
        platform = self._get_effective_platform()
        self._set_default_package_manager(platform)

        # Populate the install command
        self._refresh_install_command()

    @api.onchange('package_manager')
    def _onchange_package_manager(self):
        """Update install command when the generic (all-platforms) field changes"""
        self._refresh_install_command()

    @api.onchange('package_manager_windows')
    def _onchange_package_manager_windows(self):
        """Sync Windows selection → generic field, then refresh command"""
        self.package_manager = self.package_manager_windows
        self._refresh_install_command()

    @api.onchange('package_manager_linux')
    def _onchange_package_manager_linux(self):
        """Sync Linux selection → generic field, then refresh command"""
        self.package_manager = self.package_manager_linux
        self._refresh_install_command()

    @api.onchange('package_manager_macos')
    def _onchange_package_manager_macos(self):
        """Sync macOS selection → generic field, then refresh command"""
        self.package_manager = self.package_manager_macos
        self._refresh_install_command()

    @api.onchange('action_type')
    def _onchange_action_type(self):
        """Refresh the command whenever the action switches between install/uninstall"""
        self._refresh_install_command()

    # =========================================================================
    # DEPLOYMENT ACTION
    # =========================================================================
    def deploy_application(self):
        """Create deployment record and close wizard"""
        self.ensure_one()

        # Validate device is set
        if not self.device_id:
            raise UserError(_('Please select a device first'))

        # Validate device is online
        if self.device_id and self.device_id.last_sync_time:
            time_diff = datetime.now() - self.device_id.last_sync_time
            if time_diff.total_seconds() > 300:  # 5 minutes
                _logger.warning(
                    f"Device {self.device_id.asset_name} may be offline "
                    f"(last sync: {self.device_id.last_sync_time})"
                )

        # Determine application name
        app_name = self.application_name
        if self.application_source == 'preset' and self.application_preset and self.application_preset != 'custom':
            app_name = self.PRESET_LABELS.get(self.application_preset, self.application_name)
        if not app_name:
            raise UserError(_('Please provide an Application Name'))

        # Resolve effective package manager
        if self.application_source == 'url':
            effective_pm = 'url'
        else:
            platform = self._get_effective_platform()
            if platform == 'windows':
                effective_pm = self.package_manager_windows or self.package_manager
            elif platform == 'linux':
                effective_pm = self.package_manager_linux or self.package_manager
            elif platform == 'macos':
                effective_pm = self.package_manager_macos or self.package_manager
            else:
                effective_pm = self.package_manager
            if not effective_pm:
                raise UserError(_('Please select a Package Manager'))

        # Validate URL source
        if self.application_source == 'url' and self.action_type == 'install':
            if not self.installer_url:
                raise UserError(_('Please provide an Installer URL'))
            if not self.install_command:
                raise UserError(_('Could not generate install command. Please fill in the Installer URL'))

        # Create deployment record
        try:
            vals = {
                'device_id':          self.device_id.id,
                'application_name':   app_name,
                'application_source': self.application_source,
                'package_manager':    effective_pm,
                'action_type':        self.action_type or 'install',
                'install_command':    self.install_command,
                'notes':              self.notes,
                'status':             'pending',
            }
            if self.application_source == 'url':
                vals['installer_url']  = self.installer_url
                vals['installer_type'] = self._get_effective_installer_type()
                vals['installer_args'] = False  # silent flags are hardcoded
            deployment = self.env['asset_management.app_deployment'].create(vals)

            _logger.info(
                f"Created deployment {deployment.name} for {app_name} "
                f"on {self.device_id.asset_name}"
            )

            # Show success notification
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('🗑️ Uninstall Queued') if self.action_type == 'uninstall' else _('🚀 Deployment Created'),
                    'message': _('{action} task created for {device_name}').format(
                        action='Uninstall' if self.action_type == 'uninstall' else 'Deployment',
                        device_name=self.device_id.asset_name or self.device_id.asset_code
                    ),
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'}
                }
            }

        except Exception as e:
            _logger.error(f"Failed to create deployment: {e}", exc_info=True)
            raise UserError(_('Failed to create deployment: {error}').format(error=str(e)))
