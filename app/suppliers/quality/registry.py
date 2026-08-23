from __future__ import annotations

from .base import DEFAULT_POLICY, SupplierQualityPolicy
from .certilas import POLICY as CERTILAS_POLICY
from .kentie import POLICY as KENTIE_POLICY
from .sp_tools import POLICY as SP_TOOLS_POLICY
from .safety_jogger import POLICY as SAFETY_JOGGER_POLICY
from .valkenpower import POLICY as VALKENPOWER_POLICY


POLICIES = {
    "certilas": CERTILAS_POLICY,
    "kentie": KENTIE_POLICY,
    "edge": SAFETY_JOGGER_POLICY,
    "sp tools": SP_TOOLS_POLICY,
    "sp-tools": SP_TOOLS_POLICY,
    "safety jogger": SAFETY_JOGGER_POLICY,
    "valkenpower": VALKENPOWER_POLICY,
}


def quality_policy_for(
    vendor: object = None, *, supplier_slug: object = None,
) -> SupplierQualityPolicy:
    route_key = str(supplier_slug or "").strip().casefold()
    vendor_key = str(vendor or "").strip().casefold()
    return POLICIES.get(route_key) or POLICIES.get(vendor_key, DEFAULT_POLICY)
