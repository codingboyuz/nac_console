"""``python -m app.cli`` - operator CLI on top of the service layer (Typer)."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import SecretStr

from app.cli.runtime import CLI_CTX, CliRuntime
from app.core.exceptions import AppError
from app.core.rbac import Role
from app.repositories.base import ListQuery
from app.services.command_service import CommandRequest
from app.services.container import ServiceContainer

app = typer.Typer(help="Huawei NAC platform CLI", no_args_is_help=True)
user_app = typer.Typer(no_args_is_help=True, help="user commands")
device_app = typer.Typer(no_args_is_help=True, help="device commands")
vlan_app = typer.Typer(no_args_is_help=True, help="vlan commands")
interface_app = typer.Typer(no_args_is_help=True, help="interface commands")
mac_app = typer.Typer(no_args_is_help=True, help="mac commands")
nac_app = typer.Typer(no_args_is_help=True, help="nac commands")
job_app = typer.Typer(no_args_is_help=True, help="job commands")
openapi_app = typer.Typer(no_args_is_help=True, help="openapi commands")
vault_app = typer.Typer(no_args_is_help=True, help="vault commands")
radius_app = typer.Typer(no_args_is_help=True, help="radius commands")
db_app = typer.Typer(no_args_is_help=True, help="db commands")
app.add_typer(user_app, name="user")
app.add_typer(device_app, name="device")
app.add_typer(vlan_app, name="vlan")
app.add_typer(interface_app, name="interface")
app.add_typer(mac_app, name="mac")
app.add_typer(nac_app, name="nac")
app.add_typer(job_app, name="job")
app.add_typer(openapi_app, name="openapi")
app.add_typer(vault_app, name="vault")
app.add_typer(radius_app, name="radius")
app.add_typer(db_app, name="db")


@app.callback()
def _setup() -> None:
    """Logs go to stderr so --json output on stdout stays parseable."""
    import sys

    from app.core.config import get_settings
    from app.core.logging import configure_logging

    configure_logging("WARNING", get_settings().log_json, stream=sys.stderr)


JsonOpt = Annotated[bool, typer.Option("--json", help="machine-readable output")]


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if hasattr(value, "__table__"):
        return {
            c.name: _plain(getattr(value, c.key, None))
            for c in value.__table__.columns
            if "secret" not in c.name and "ciphertext" not in c.name and c.name != "password_hash"
        }
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _emit(data: Any, as_json: bool) -> None:
    plain = _plain(data)
    if as_json:
        typer.echo(json.dumps(plain, indent=2, default=str))
        return
    rows = plain if isinstance(plain, list) else [plain]
    for row in rows:
        typer.echo(
            "  ".join(f"{k}={v}" for k, v in row.items()) if isinstance(row, dict) else str(row)
        )


def _call(fn: Any) -> Any:
    try:
        return CliRuntime().run(fn)
    except AppError as exc:
        typer.echo(f"error: {exc.code}: {exc.message}", err=True)
        raise typer.Exit(code=2) from exc


# --- users / bootstrap ------------------------------------------------------------------------
@user_app.command("create")
def user_create(
    username: str,
    role: Annotated[Role, typer.Option()] = Role.ADMIN,
    service_account: Annotated[bool, typer.Option("--service-account")] = False,
    password: Annotated[
        str, typer.Option(prompt=True, hide_input=True, confirmation_prompt=True)
    ] = "",  # nosec B107
    as_json: JsonOpt = False,
) -> None:
    """Bootstrap an ADMIN or create the service account used by schedules/CLI."""
    from app.schemas.auth import UserCreate

    async def run(s: ServiceContainer) -> Any:
        return await s.users.create(
            UserCreate(username=username, password=SecretStr(password), role=role),
            service_account=service_account,
        )

    user = _call(run)
    _emit({"id": str(user.id), "username": user.username, "role": user.role.value}, as_json)


# --- devices -----------------------------------------------------------------------------------
@device_app.command("list")
def device_list(as_json: JsonOpt = False, limit: int = 100) -> None:
    async def run(s: ServiceContainer) -> Any:
        principal = await CliRuntime().principal(s)
        return (await s.devices.search(principal, ListQuery(limit=limit, sort="hostname"))).items

    _emit(
        [
            {
                "id": str(d.id),
                "hostname": d.hostname,
                "mgmt_ip": d.mgmt_ip,
                "family": d.family.value,
                "status": d.status.value,
            }
            for d in _call(run)
        ],
        as_json,
    )


@device_app.command("test")
def device_test(device_id: uuid.UUID, as_json: JsonOpt = False) -> None:
    async def run(s: ServiceContainer) -> Any:
        return await s.commands.test_connection(await CliRuntime().principal(s), CLI_CTX, device_id)

    _emit(_call(run), as_json)


@device_app.command("command")
def device_command(
    device_id: uuid.UUID,
    command: str,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    confirm: Annotated[
        str | None, typer.Option("--confirm", help="confirmation token from --dry-run")
    ] = None,
    reason: Annotated[str | None, typer.Option()] = None,
    as_json: JsonOpt = False,
) -> None:
    """Run one classified command.

    DANGEROUS commands need --dry-run first, then --confirm TOKEN --reason.
    """
    req = CommandRequest(
        command,
        dry_run=dry_run,
        confirm=confirm is not None,
        confirmation_token=confirm,
        reason=reason,
    )

    async def run(s: ServiceContainer) -> Any:
        return await s.commands.execute(await CliRuntime().principal(s), CLI_CTX, device_id, req)

    result = _call(run)
    _emit(
        {
            "command": result.command,
            "category": result.classification.category.value,
            "reason": result.classification.reason,
            "confirmation_token": result.confirmation_token,
            "output": result.output,
        },
        as_json,
    )


@device_app.command("backup")
def device_backup(device_id: uuid.UUID, as_json: JsonOpt = False) -> None:
    from app.models.backup import BackupTrigger

    async def run(s: ServiceContainer) -> Any:
        principal = await CliRuntime().principal(s)
        device = await s.devices.get(principal, device_id)
        row, created = await s.backups.backup(principal, device, BackupTrigger.MANUAL)
        return {
            "backup_id": str(row.id),
            "created": created,
            "status": row.status,
            "sha256": row.sha256,
        }

    _emit(_call(run), as_json)


# --- L2 ----------------------------------------------------------------------------------------
@vlan_app.command("list")
def vlan_list(device_id: uuid.UUID, as_json: JsonOpt = False) -> None:
    async def run(s: ServiceContainer) -> Any:
        return await s.vlans.list_live(await CliRuntime().principal(s), device_id)

    _emit(_call(run), as_json)


@vlan_app.command("create")
def vlan_create(
    device_id: uuid.UUID,
    vlan_id: int,
    name: Annotated[str | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    as_json: JsonOpt = False,
) -> None:
    from app.services.change_service import ChangeOptions

    async def run(s: ServiceContainer) -> Any:
        return await s.vlans.create(
            await CliRuntime().principal(s),
            CLI_CTX,
            device_id,
            {vlan_id: name},
            ChangeOptions(dry_run=dry_run),
        )

    result = _call(run)
    _emit(
        {"status": result.status, "category": result.category.value, "lines": result.lines}, as_json
    )


@interface_app.command("list")
def interface_list(device_id: uuid.UUID, refresh: bool = False, as_json: JsonOpt = False) -> None:
    async def run(s: ServiceContainer) -> Any:
        principal = await CliRuntime().principal(s)
        if refresh:
            return await s.interfaces.refresh(principal, device_id)
        return await s.interfaces.cached(principal, device_id)

    _emit(_call(run), as_json)


@mac_app.command("search")
def mac_search(
    mac: Annotated[str | None, typer.Option()] = None,
    vlan: Annotated[int | None, typer.Option()] = None,
    as_json: JsonOpt = False,
) -> None:
    async def run(s: ServiceContainer) -> Any:
        return await s.macs.search(await CliRuntime().principal(s), mac=mac, vlan=vlan)

    _emit(_call(run), as_json)


# --- NAC / jobs --------------------------------------------------------------------------------
@nac_app.command("endpoints")
def nac_endpoint_list(as_json: JsonOpt = False, limit: int = 100) -> None:
    async def run(s: ServiceContainer) -> Any:
        await CliRuntime().principal(s)
        return (await s.nac.endpoints(ListQuery(limit=limit, sort="-last_seen"))).items

    _emit(_call(run), as_json)


@nac_app.command("endpoint")
def nac_endpoint_show(mac: str, as_json: JsonOpt = False) -> None:
    async def run(s: ServiceContainer) -> Any:
        await CliRuntime().principal(s)
        return await s.nac.endpoint(mac)

    _emit(_call(run), as_json)


@job_app.command("list")
def job_list(as_json: JsonOpt = False, limit: int = 50) -> None:
    async def run(s: ServiceContainer) -> Any:
        return (
            await s.jobs.list(
                await CliRuntime().principal(s), ListQuery(limit=limit, sort="-created_at")
            )
        ).items

    _emit(
        [
            {"id": str(j.id), "type": j.type, "status": j.status.value, "progress": j.progress}
            for j in _call(run)
        ],
        as_json,
    )


@job_app.command("show")
def job_show(job_id: uuid.UUID, as_json: JsonOpt = False) -> None:
    async def run(s: ServiceContainer) -> Any:
        job, results = await s.jobs.get(await CliRuntime().principal(s), job_id)
        return {
            "id": str(job.id),
            "type": job.type,
            "status": job.status.value,
            "progress": job.progress,
            "results": [
                {"device_id": str(r.device_id), "status": r.status.value, "error": r.error}
                for r in results
            ],
        }

    _emit(_call(run), as_json)


# --- maintenance -------------------------------------------------------------------------------
@openapi_app.command("export")
def openapi_export(output: Annotated[Path, typer.Option()] = Path("openapi.json")) -> None:
    """Write the OpenAPI 3.1 document for the React/TypeScript client generator."""
    from app.main import create_app

    output.write_text(
        json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    typer.echo(f"wrote {output}")


@vault_app.command("rotate-keys")
def vault_rotate() -> None:
    """Re-encrypt every credential with CREDENTIAL_ACTIVE_KEY_ID (keep old keys until done)."""

    async def run(s: ServiceContainer) -> int:
        return await s.credentials.rotate_keys()

    typer.echo(f"rotated {_call(run)} credential(s)")


@db_app.command("maintain")
def db_maintain() -> None:
    """Create upcoming metric partitions and apply retention."""

    async def run(s: ServiceContainer) -> Any:
        return await s.monitoring.maintain()

    _emit(_call(run), True)


@radius_app.command("serve")
def radius_serve(
    secret: Annotated[str, typer.Option(prompt=True, hide_input=True)] = "",  # nosec B107
    host: str = "127.0.0.1",
    auth_port: int = 1812,
    acct_port: int = 1813,
) -> None:
    """Development-only pyrad reference server (MAB + accounting). Refused in staging/prod."""
    import asyncio

    from app.nac.reference_server import serve

    async def run(s: ServiceContainer) -> None:
        await serve(s.settings, s.nac, secret, host=host, auth_port=auth_port, acct_port=acct_port)
        await asyncio.Event().wait()

    _call(run)


def main() -> None:
    app()
