# proxmox.py
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import requests

from config import settings


@dataclass
class ProxmoxError(RuntimeError):
    message: str
    status_code: Optional[int] = None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.message} (HTTP {self.status_code})"


class ProxmoxClient:
    def __init__(
        self,
        host: str,
        token_id: str,
        token_secret: str,
        port: int = 8006,
        node: str = "proxmox",
        verify_ssl: bool = False,
        timeout: float = 5.0,
    ) -> None:
        if not token_id or not token_secret:
            raise ProxmoxError(
                "Proxmox token puuttuu. Täytä secrets.py: PROXMOX_TOKEN_ID ja PROXMOX_TOKEN_SECRET."
            )

        self.base = f"https://{host}:{port}/api2/json"
        self.node = node
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"PVEAPIToken={token_id}={token_secret}",
                "Accept": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base + path

    def get(self, path: str) -> Dict[str, Any]:
        r = self.session.get(self._url(path), timeout=self.timeout, verify=self.verify_ssl)
        if not r.ok:
            raise ProxmoxError("Proxmox GET epäonnistui", r.status_code)
        return r.json()

    def post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        r = self.session.post(self._url(path), data=data or {}, timeout=self.timeout, verify=self.verify_ssl)
        if not r.ok:
            raise ProxmoxError("Proxmox POST epäonnistui", r.status_code)
        return r.json()

    # ---- VM helpers ----

    def get_vm_status(self, vmid: int) -> str:
        j = self.get(f"/nodes/{self.node}/qemu/{vmid}/status/current")
        data = j.get("data") or {}
        return str(data.get("status", "unknown"))

    def start_vm(self, vmid: int) -> str:
        j = self.post(f"/nodes/{self.node}/qemu/{vmid}/status/start")
        return str((j.get("data") or "")).strip()

    def shutdown_vm(self, vmid: int) -> str:
        j = self.post(f"/nodes/{self.node}/qemu/{vmid}/status/shutdown")
        return str((j.get("data") or "")).strip()

    def stop_vm(self, vmid: int) -> str:
        # Force stop
        j = self.post(f"/nodes/{self.node}/qemu/{vmid}/status/stop")
        return str((j.get("data") or "")).strip()

    def wait_for_status(self, vmid: int, wanted: str, timeout_s: int = 60, poll_s: float = 2.0) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                st = self.get_vm_status(vmid)
                if st == wanted:
                    return True
            except Exception:
                pass
            time.sleep(poll_s)
        return False


@lru_cache(maxsize=1)
def client() -> ProxmoxClient:
    return ProxmoxClient(
        host=settings.PROXMOX_HOST,
        port=settings.PROXMOX_PORT,
        node=settings.PROXMOX_NODE,
        token_id=settings.PROXMOX_TOKEN_ID,
        token_secret=settings.PROXMOX_TOKEN_SECRET,
        verify_ssl=settings.PROXMOX_VERIFY_SSL,
    )


def get_vm_status(vmid: int) -> str:
    return client().get_vm_status(vmid)


def start_vm(vmid: int, wait_running: bool = True, timeout_s: int = 60) -> Tuple[bool, str]:
    try:
        st = get_vm_status(vmid)
        if st == "running":
            return True, "VM on jo käynnissä."
        upid = client().start_vm(vmid)
        if wait_running:
            ok = client().wait_for_status(vmid, "running", timeout_s=timeout_s)
            return ok, f"Start komento lähetetty (UPID={upid})."
        return True, f"Start komento lähetetty (UPID={upid})."
    except Exception as e:
        return False, str(e)


def shutdown_vm(vmid: int, wait_stopped: bool = False, timeout_s: int = 90) -> Tuple[bool, str]:
    try:
        st = get_vm_status(vmid)
        if st != "running":
            return True, f"VM ei ole käynnissä (status={st})."
        upid = client().shutdown_vm(vmid)
        if wait_stopped:
            ok = client().wait_for_status(vmid, "stopped", timeout_s=timeout_s)
            return ok, f"Shutdown komento lähetetty (UPID={upid})."
        return True, f"Shutdown komento lähetetty (UPID={upid})."
    except Exception as e:
        return False, str(e)


def stop_vm(vmid: int, wait_stopped: bool = True, timeout_s: int = 60) -> Tuple[bool, str]:
    try:
        upid = client().stop_vm(vmid)
        if wait_stopped:
            ok = client().wait_for_status(vmid, "stopped", timeout_s=timeout_s)
            return ok, f"Force stop lähetetty (UPID={upid})."
        return True, f"Force stop lähetetty (UPID={upid})."
    except Exception as e:
        return False, str(e)
