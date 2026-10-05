"""FEFO consume: earliest expiry first among eligible on-shelf lots.

资格规则(两种资格共用一条硬过滤):
  - 临期资格: on_shelf 且 qty_remain > 0 的批, 按到期日先到期先扣。
  - 未扫过期资格: 到期日已早于今天但仍 on_shelf(下架扫描尚未扫到)的批,
    同样具备被扣资格, 且按 FEFO 天然排在最前 —— 不允许出现"全层仍有余量
    却永远扣不到、顶条还当紧急"的死批。
  - 脏负量批(qty_remain <= 0)无论哪种资格都不得进入 deductions。
"""

def is_eligible(lot: dict) -> bool:
    """可被消费扣减的唯一硬条件: 剩余量为正。脏负量批永不合格。"""
    return float(lot.get("qty_remain", 0)) > 0

def is_expired_unswept(lot: dict, today: str | None) -> bool:
    """到期日已早于 today 但仍 on_shelf(尚未被下架扫描标记)的批。"""
    exp = lot.get("expiry")
    return bool(exp) and today is not None and exp < today

def sort_lots_fefo(lots: list[dict]) -> list[dict]:
    return sorted(
        [l for l in lots if is_eligible(l)],
        key=lambda l: (l.get("expiry") or "9999-99-99", l.get("id") or 0),
    )

def consume_fefo(lots: list[dict], qty: float, today: str | None = None) -> dict:
    """Return deductions list and leftover demand. Mutates copies only.

    Each deduction carries ``expired_unswept`` so callers(回包/前端)能区分
    本次扣到的是临期批还是过期仍在架的批。
    """
    need = float(qty)
    if need <= 0:
        return {"ok": False, "reason": "qty_non_positive", "deductions": [], "short": 0.0}
    ordered = sort_lots_fefo(lots)
    deductions = []
    for lot in ordered:
        if need <= 0:
            break
        avail = float(lot["qty_remain"])
        take = min(avail, need)
        deductions.append({
            "lot_id": lot["id"],
            "take": take,
            "expiry": lot.get("expiry"),
            "expired_unswept": is_expired_unswept(lot, today),
        })
        need -= take
    if need > 1e-9:
        return {"ok": False, "reason": "short", "deductions": deductions, "short": round(need, 3)}
    return {"ok": True, "reason": "", "deductions": deductions, "short": 0.0}

def expire_lots(lots: list[dict], today: str) -> list[int]:
    """Ids that should leave shelf: remaining>0 and expiry < today."""
    out = []
    for l in lots:
        exp = l.get("expiry")
        if exp and exp < today and float(l.get("qty_remain", 0)) > 0:
            out.append(l["id"])
    return out
