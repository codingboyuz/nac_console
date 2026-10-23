"""Alert evaluation (for-duration, hysteresis), flapping, scope/maintenance, webhook signing,
discovery CIDR guard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import ValidationFailed
from app.domain import alerting as a
from app.services.notifications import sign
from tests.conftest import make_settings

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
CPU = a.ThresholdRule("gt", 85, clear_threshold=70, for_seconds=60)


def test_for_duration_then_fire_then_hysteresis_resolve() -> None:
    state = a.EvalState(False, None)
    t, state = a.evaluate(CPU, 90, state, T0)
    assert t is a.Transition.PENDING
    t, state = a.evaluate(CPU, 95, state, T0 + timedelta(seconds=30))
    assert t is a.Transition.PENDING
    t, state = a.evaluate(CPU, 91, state, T0 + timedelta(seconds=61))
    assert t is a.Transition.FIRE
    t, state = a.evaluate(
        CPU, 80, state, T0 + timedelta(seconds=90)
    )  # below threshold, above clear
    assert t is a.Transition.STILL_FIRING
    t, state = a.evaluate(CPU, 65, state, T0 + timedelta(seconds=120))
    assert t is a.Transition.RESOLVE
    assert not state.firing


def test_breach_reset_restarts_pending() -> None:
    state = a.EvalState(False, None)
    _, state = a.evaluate(CPU, 90, state, T0)
    t, state = a.evaluate(CPU, 50, state, T0 + timedelta(seconds=30))
    assert t is a.Transition.NONE
    assert state.pending_since is None
    t, _ = a.evaluate(CPU, None, state, T0)
    assert t is a.Transition.NONE


def test_less_than_rule() -> None:
    down = a.ThresholdRule("lt", 1, clear_threshold=1)
    t, state = a.evaluate(down, 0, a.EvalState(False, None), T0)
    assert t is a.Transition.FIRE
    t, _ = a.evaluate(down, 1, state, T0)
    assert t is a.Transition.RESOLVE


def test_flapping_and_scope() -> None:
    stamps = [T0 + timedelta(minutes=i) for i in range(4)]
    assert a.is_flapping(stamps, T0 + timedelta(minutes=4), 600, 4)
    assert not a.is_flapping(stamps, T0 + timedelta(hours=2), 600, 4)
    assert a.in_scope({}, {"site": "hq"})
    assert a.in_scope({"site": "hq"}, {"site": "hq"})
    assert not a.in_scope({"site": "hq"}, {"site": "dc"})
    windows = [({"site": "hq"}, T0, T0 + timedelta(hours=1))]
    assert a.in_maintenance(windows, {"site": "hq"}, T0 + timedelta(minutes=5))
    assert not a.in_maintenance(windows, {"site": "dc"}, T0 + timedelta(minutes=5))


def test_webhook_signature_is_hmac_sha256() -> None:
    sig = sign("s3cret", "1700000000", b'{"a":1}')
    assert sig.startswith("sha256=")
    assert sig == sign("s3cret", "1700000000", b'{"a":1}')
    assert sig != sign("other", "1700000000", b'{"a":1}')


@pytest.mark.parametrize(
    ("cidr", "ok"),
    [
        ("192.0.2.0/24", True),
        ("10.0.4.0/22", True),
        ("10.0.0.0/16", False),  # larger than /22
        ("198.51.100.0/24", False),  # not in allowed subnets
        ("2001:db8::/120", False),
        ("garbage", False),
    ],
)
def test_discovery_cidr_guard(cidr: str, ok: bool) -> None:
    from app.services.discovery_service import DiscoveryService

    svc = DiscoveryService(make_settings(), None, None, None, None)  # type: ignore[arg-type]
    if ok:
        assert svc.validate_cidr(cidr)
    else:
        with pytest.raises(ValidationFailed):
            svc.validate_cidr(cidr)
