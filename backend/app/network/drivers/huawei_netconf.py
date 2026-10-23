"""ncclient NETCONF driver for Huawei devices. Blocking: worker / threadpool only.

Safe change flow (``apply_edit``): lock -> get-config snapshot -> edit-config (candidate when
advertised) -> validate (if advertised) -> commit (confirmed-commit when advertised) -> verify
-> confirming commit; on any failure discard-changes / cancel-commit, then unlock.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from typing import Any

from app.core.exceptions import CommandRejected, DeviceError, UnsupportedCapability
from app.core.logging import get_logger
from app.domain.devices import DeviceFamily, DeviceProtocol
from app.network.drivers.base import DriverBase, elapsed_ms, retry_connect
from app.network.errors import map_exception
from app.network.netconf_models import ModelRegistry
from app.network.ports import CommandOutput, ConfigResult, ConnectionParams, DeviceFacts

log = get_logger(__name__)
Connector = Callable[..., Any]
Verifier = Callable[[], bool]


def _default_connector() -> Connector:
    from ncclient import manager

    connect: Connector = manager.connect_ssh
    return connect


class HuaweiNetconfDriver(DriverBase):
    protocol = DeviceProtocol.NETCONF

    def __init__(self, params: ConnectionParams, *, connector: Connector | None = None) -> None:
        self.params = params
        self._connector = connector or _default_connector()
        self._mgr: Any = None
        self._key_file: str | None = None
        self.registry = ModelRegistry()

    def connect(self) -> None:
        if self._mgr is not None:
            return
        p = self.params
        kwargs: dict[str, Any] = {
            "host": p.host,
            "port": p.port,
            "username": p.username,
            "password": p.password.get_secret_value() if p.password else None,
            "timeout": p.timeout,
            "allow_agent": False,
            "look_for_keys": False,
            "hostkey_verify": p.pinned_host_key is not None or p.strict_host_key,
            "device_params": {"name": "huaweiyang" if p.family is DeviceFamily.CE else "huawei"},
        }
        if p.pinned_host_key is not None:
            kwargs["hostkey_b64"] = p.pinned_host_key.public_key_b64
        if p.private_key is not None:
            kwargs["key_filename"] = self._write_key()

        def attempt() -> Any:
            return self._connector(**kwargs)

        self._mgr = retry_connect(p, attempt)
        self.registry = ModelRegistry.from_capabilities(list(self._mgr.server_capabilities))

    def _write_key(self) -> str:
        assert self.params.private_key is not None  # noqa: S101
        fd, path = tempfile.mkstemp(prefix="nac-nc-", suffix=".key")
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(self.params.private_key.get_secret_value())
        os.chmod(path, 0o600)
        self._key_file = path
        return path

    def disconnect(self) -> None:
        mgr, self._mgr = self._mgr, None
        if mgr is not None:
            try:
                mgr.close_session()
            except Exception as exc:
                log.debug("netconf_close_failed", error=type(exc).__name__)
        if self._key_file:
            os.unlink(self._key_file)
            self._key_file = None

    def _m(self) -> Any:
        if self._mgr is None:
            self.connect()
        return self._mgr

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._m(), name)(*args, **kwargs)
        except Exception as exc:
            if type(exc).__name__ == "RPCError":
                raise CommandRejected(
                    f"NETCONF {name} rejected: {getattr(exc, 'message', '')}"
                ) from exc
            raise map_exception(exc, self.params.host) from exc

    # --- DeviceDriver port --------------------------------------------------------------
    def run_show(self, command: str) -> CommandOutput:
        raise UnsupportedCapability("NETCONF devices expose data via get/get-config, not CLI")

    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        raise UnsupportedCapability("use apply_edit() with an XML payload for NETCONF devices")

    def capabilities(self) -> DeviceFacts:
        self._m()
        return DeviceFacts(
            family=self.params.family,
            vrp_version=None,
            software_version=None,
            model=None,
            capabilities={"netconf": self.registry.to_dict(), "transport": "netconf"},
        )

    def health(self) -> bool:
        return bool(self._mgr is not None and self._mgr.connected)

    # --- NETCONF operations ---------------------------------------------------------------
    def get(self, filter_xml: str | None = None) -> str:
        reply = self._call("get", filter=("subtree", filter_xml) if filter_xml else None)
        return str(reply.data_xml)

    def get_config(self, source: str = "running", filter_xml: str | None = None) -> str:
        flt = ("subtree", filter_xml) if filter_xml else None
        return str(self._call("get_config", source=source, filter=flt).data_xml)

    def apply_edit(
        self,
        config_xml: str,
        *,
        verify: Verifier | None = None,
        confirm_timeout: int = 120,
        filter_xml: str | None = None,
    ) -> ConfigResult:
        """Apply one edit-config payload with snapshot, validation, commit and rollback."""
        start = time.perf_counter()
        self._m()
        reg = self.registry
        target = reg.edit_target
        if target == "running" and not reg.features.get("writable_running"):
            raise UnsupportedCapability(
                "device advertises neither :candidate nor :writable-running"
            )
        confirmed = target == "candidate" and reg.features.get("confirmed_commit", False)
        snapshot = self.get_config("running", filter_xml)
        locked = pending_confirm = False
        try:
            self._call("lock", target=target)
            locked = True
            self._call("edit_config", target=target, config=config_xml, default_operation="merge")
            if reg.features.get("validate"):
                self._call("validate", source=target)
            if target == "candidate":
                if confirmed:
                    self._call("commit", confirmed=True, timeout=str(confirm_timeout))
                    pending_confirm = True
                else:
                    self._call("commit")
            if verify is not None and not verify():
                raise DeviceError("post-change verification failed")
            if pending_confirm:
                self._call("commit")  # confirming commit
                pending_confirm = False
        except Exception:
            self._rollback(target, pending_confirm)
            raise
        finally:
            if locked:
                try:
                    self._call("unlock", target=target)
                except DeviceError as exc:
                    log.warning("netconf_unlock_failed", error=exc.code)
        return ConfigResult(
            (config_xml,), snapshot, target == "candidate", False, elapsed_ms(start)
        )

    def _rollback(self, target: str, pending_confirm: bool) -> None:
        """Undo: cancel a pending confirmed commit, then drop candidate edits."""
        steps = (["cancel_commit"] if pending_confirm else []) + (
            ["discard_changes"] if target == "candidate" else []
        )
        for step in steps:
            try:
                self._call(step)
            except DeviceError as exc:
                log.error("netconf_rollback_step_failed", step=step, error=exc.code)
