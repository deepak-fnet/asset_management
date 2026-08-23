"""
Kaspersky Security Center (KSC) REST API service layer.

Provides authentication, remote installation task creation and status polling
against a Kaspersky Security Center server using its OpenAPI (REST) interface.
"""
import logging
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_logger = logging.getLogger(__name__)


class KSCError(Exception):
    """Raised when KSC API returns an error."""


class KasperskySecurityCenterService:
    """
    Thin wrapper around the Fake Kaspersky Security Center API.
    """

    def __init__(self, base_url, username, password, verify_ssl=False, timeout=15):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._session = requests.Session()
        self._session.verify = verify_ssl

    def _url(self, path):
        return f"{self.base_url}{path}"

    def test_connection(self):
        """Test connectivity to the Fake API."""
        url = self._url("/api/test-connection")
        payload = {
            "username": self.username,
            "password": self.password,
        }
        try:
            resp = self._session.post(url, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return {"ok": True, "message": data.get("message", "Connected to Fake API")}
            else:
                data = resp.json()
                return {"ok": False, "message": data.get("message", "Connection failed")}
        except Exception as e:
            return {"ok": False, "message": f"Connection error: {e}"}

    def create_install_task(self, hostname, package_name):
        """Create a deploy task on the Fake API."""
        url = self._url("/api/deploy")
        payload = {
            "hostname": hostname,
            "package_name": package_name,
        }
        try:
            resp = self._session.post(url, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("task_id")
            else:
                raise Exception(f"Deploy failed: {resp.text}")
        except Exception as e:
            raise Exception(f"Deploy error: {e}")

    def get_task_status(self, task_id):
        """Poll the status of a task from the Fake API."""
        url = self._url(f"/api/status/{task_id}")
        try:
            resp = self._session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": data.get("status"),
                    "progress": data.get("progress"),
                    "message": "Deployment in progress" if data.get("status") == "in_progress" else "Completed"
                }
            else:
                return {"status": "failed", "progress": 0, "message": f"Error: {resp.status_code}"}
        except Exception as e:
            return {"status": "failed", "progress": 0, "message": str(e)}

    def authenticate(self):
        # Fake API doesn't need separate authentication call
        pass

    def logout(self):
        # Fake API doesn't need logout
        pass
