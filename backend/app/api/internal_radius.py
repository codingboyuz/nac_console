"""Internal RADIUS callbacks for FreeRADIUS rlm_rest (network-restricted, token, rate-limited).

authorize: 200 + reply attributes (accept) or 401 + Reply-Message (reject).
accounting: 204.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import Ctx, Services, rate_limited
from app.core.exceptions import ValidationFailed
from app.core.net_guard import require_internal_network, require_internal_token
from app.domain.mac import InvalidMac, normalize_mac
from app.schemas.nac import DhcpFingerprintIn, RlmRestBody
from app.services.nac_service import AccountingRequest, RadiusRequest

router = APIRouter(
    tags=["internal"],
    include_in_schema=True,
    dependencies=[
        Depends(require_internal_network),
        Depends(require_internal_token),
        Depends(rate_limited("internal", "rate_limit_internal")),
    ],
)


def _mac(body: RlmRestBody) -> str:
    raw = body.attr("Calling-Station-Id") or body.attr("User-Name") or ""
    try:
        return normalize_mac(raw)
    except InvalidMac as exc:
        raise ValidationFailed("Calling-Station-Id must carry the client MAC") from exc


@router.post("/radius/authorize", summary="FreeRADIUS rlm_rest authorize/post-auth callback")
async def authorize(body: RlmRestBody, services: Services, ctx: Ctx) -> Response:
    is_eap = body.attr("EAP-Message") is not None or body.attr("auth_method") == "dot1x"
    req = RadiusRequest(
        mac=_mac(body),
        username=body.attr("User-Name"),
        nas_ip=body.attr("NAS-IP-Address"),
        nas_port_id=body.attr("NAS-Port-Id"),
        auth_method="dot1x" if is_eap else "mab",
        user_groups=frozenset(body.attrs("user_groups") or body.attrs("Group")),
        framed_ip=body.attr("Framed-IP-Address"),
    )
    decision = await services.nac.authorize(req, ctx)
    payload: dict[str, Any] = {f"reply:{k}": v for k, v in decision.reply.items()}
    payload["policy"] = decision.policy_name
    if decision.result == "reject":
        return JSONResponse(payload, status_code=status.HTTP_401_UNAUTHORIZED)
    return JSONResponse(payload, status_code=status.HTTP_200_OK)


@router.post("/radius/accounting", status_code=status.HTTP_204_NO_CONTENT)
async def accounting(body: RlmRestBody, services: Services) -> None:
    status_type = body.attr("Acct-Status-Type") or "Interim-Update"
    nas_ip = body.attr("NAS-IP-Address")
    session_id = body.attr("Acct-Session-Id")
    if not nas_ip or not session_id:
        raise ValidationFailed("NAS-IP-Address and Acct-Session-Id are required")
    await services.nac.accounting(
        AccountingRequest(
            status_type=status_type,
            session_id=session_id,
            mac=_mac(body),
            username=body.attr("User-Name"),
            nas_ip=nas_ip,
            nas_port_id=body.attr("NAS-Port-Id"),
            framed_ip=body.attr("Framed-IP-Address"),
            input_octets=int(body.attr("Acct-Input-Octets") or 0),
            output_octets=int(body.attr("Acct-Output-Octets") or 0),
            terminate_cause=body.attr("Acct-Terminate-Cause"),
        )
    )


@router.post(
    "/profiling/dhcp",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="DHCP fingerprint feed (DHCP server / FreeRADIUS dhcp) for profiling",
)
async def dhcp_fingerprint(body: DhcpFingerprintIn, services: Services, request: Request) -> None:
    await services.nac.ingest_dhcp(
        body.mac, body.parameter_request_list, body.vendor_class, body.hostname
    )
