"""
Subscriber (CRM) routes.

Operator-only, every one of them. These records carry caller names, MSISDNs,
account balances, and payment status; the previous engine served the entire
subscriber base to any unauthenticated request.

The PATCH route closes the other half of that gap: the store's field-level
write existed but had no caller and no route, so nothing in the system could
ever mark a router rebooted or a payment captured.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.schemas.common import ErrorResponse
from app.api.v1.schemas.crm import CustomerOut, CustomerUpdateRequest
from app.core.deps import CrmDep
from app.core.errors import CustomerNotFoundError
from app.core.security import OperatorKeyDep

router = APIRouter(prefix="/customers", tags=["crm"])

_AUTH_RESPONSES: dict[int | str, dict] = {401: {"model": ErrorResponse}}


@router.get(
    "",
    response_model=list[CustomerOut],
    responses=_AUTH_RESPONSES,
    summary="List subscribers",
)
async def list_customers(crm: CrmDep, _key: OperatorKeyDep) -> list[CustomerOut]:
    records = await crm.list_customers()
    return [CustomerOut.from_domain(record) for record in records]


@router.get(
    "/{phone_number}",
    response_model=CustomerOut,
    responses={**_AUTH_RESPONSES, 404: {"model": ErrorResponse}},
    summary="Look up one subscriber by MSISDN",
)
async def get_customer(phone_number: str, crm: CrmDep, _key: OperatorKeyDep) -> CustomerOut:
    record = await crm.get_customer(phone_number)
    if record is None:
        raise CustomerNotFoundError(f"No subscriber found for {phone_number}")
    return CustomerOut.from_domain(record)


@router.patch(
    "/{phone_number}",
    response_model=CustomerOut,
    responses={**_AUTH_RESPONSES, 400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Write one field on a subscriber record",
    description=(
        "Single-field, auditable write — e.g. flipping `router_status` to `Online` "
        "after a guided reboot, or advancing `payment_status` once a payment clears. "
        "Identity fields (name, account id, MSISDN) are not writable here."
    ),
)
async def update_customer(
    phone_number: str,
    payload: CustomerUpdateRequest,
    crm: CrmDep,
    _key: OperatorKeyDep,
) -> CustomerOut:
    record = await crm.update_field(phone_number, payload.field, payload.value)
    return CustomerOut.from_domain(record)
