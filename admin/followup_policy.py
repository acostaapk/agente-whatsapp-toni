"""Pure policy helpers for WhatsApp follow-up timing."""

from __future__ import annotations


FOLLOWUP_MINUTES = 30
CUSTOMER_SERVICE_WINDOW_SECONDS = 24 * 60 * 60


def purchase_check_due(
    *,
    offer_ts: float,
    latest_customer_ts: float,
    now_ts: float,
    already_purchased: bool,
    already_sent: bool,
) -> bool:
    """Whether the one 30-minute check-in is due inside Meta's service window."""
    if already_purchased or already_sent or not latest_customer_ts:
        return False
    if latest_customer_ts > offer_ts or now_ts < offer_ts:
        return False
    if now_ts - offer_ts < FOLLOWUP_MINUTES * 60:
        return False
    return 0 <= now_ts - latest_customer_ts < CUSTOMER_SERVICE_WINDOW_SECONDS

