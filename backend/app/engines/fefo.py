"""FEFO consume with two-tier eligibility.

Eligibility tiers (today given):
  fresh   -- positive, clean, expiry >= today: normal FEFO order.
  expired -- expiry < today but still on_shelf and positive/clean:
             "未扫过期" fallback tier, only consumed once fresh supply
             runs short, so an unscanned-expired lot is reachable while
             a layer still has margin instead of being pinned forever.

Dirty lots (data_quality != clean) and non-positive remaining lots are
excluded under BOTH tiers: a dirty/negative lot can never appear in
deductions, and cannot cover demand.

With today=None the engine keeps the legacy behaviour (no expiry tier).
"""


def _eligible(lot: dict, today: str | None) -> tuple[bool, str]:
    try:
        avail = float(lot.get("qty_remain", 0))
    except (TypeError, ValueError):
        return False, ""
    if avail <= 0:
        return False, ""
    if lot.get("data_quality", "clean") != "clean":
        return False, ""
    if today is not None:
        exp = lot.get("expiry")
        if not exp:
            # unknown expiry is treated as fresh (never expires for ordering)
            return True, "fresh"
        return (True, "fresh") if exp >= today else (True, "expired")
    return True, "fresh"


def _sort_key(lot: dict):
    return (lot.get("expiry") or "9999-99-99", lot.get("id") or 0)


def sort_lots_fefo(lots: list[dict], today: str | None = None) -> list[dict]:
    """Eligible lots, fresh tier first then expired fallback, FEFO inside tier."""
    tiers = {"fresh": [], "expired": []}
    for lot in lots:
        ok, tier = _eligible(lot, today)
        if ok:
            tiers[tier].append(lot)
    return sorted(tiers["fresh"], key=_sort_key) + sorted(tiers["expired"], key=_sort_key)


def consume_fefo(lots: list[dict], qty: float, today: str | None = None) -> dict:
    """Plan deductions. Mutates nothing; returned plan is applied by the caller."""
    try:
        need = float(qty)
    except (TypeError, ValueError):
        need = 0.0
    if need <= 0:
        return {"ok": False, "reason": "qty_non_positive", "deductions": [], "short": 0.0}
    ordered = sort_lots_fefo(lots, today)
    deductions = []
    for lot in ordered:
        if need <= 1e-9:
            break
        avail = float(lot["qty_remain"])
        take = min(avail, need)
        deductions.append({
            "lot_id": lot["id"],
            "take": round(take, 3),
            "expiry": lot.get("expiry"),
            "tier": _eligible(lot, today)[1],
        })
        need -= take
    if need > 1e-9:
        return {"ok": False, "reason": "short", "deductions": deductions, "short": round(need, 3)}
    return {"ok": True, "reason": "", "deductions": deductions, "short": 0.0}


def expire_lots(lots: list[dict], today: str) -> list[int]:
    """Ids that should leave shelf: remaining>0 and expiry < today.

    Sweep eligibility is intentionally independent of consume eligibility:
    taking a dirty lot off the shelf never touches its quantity, whereas a
    dirty/negative lot must never be laundered through consume deductions.
    """
    out = []
    for l in lots:
        exp = l.get("expiry")
        if exp and exp < today and float(l.get("qty_remain", 0)) > 0:
            out.append(l["id"])
    return out
