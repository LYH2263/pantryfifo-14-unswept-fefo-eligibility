from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.transactions import ConsumeError, run_consume, run_sweep

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows


def _tier(r: dict, today: str) -> str:
    if r["status"] != "on_shelf" or float(r.get("qty_remain", 0)) <= 0:
        return ""
    if r.get("data_quality", "clean") != "clean":
        return ""
    exp = r.get("expiry")
    if not exp or exp >= today:
        return "fresh"
    return "expired"


@app.get("/api/fridge")
def fridge(layer: str | None = None):
    c = connect()
    today = date.today().isoformat()
    q = """SELECT lots.*, items.name, items.layer, items.unit FROM lots
           JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf'"""
    args = []
    if layer:
        q += " AND items.layer=?"; args.append(layer)
    q += " ORDER BY items.layer, lots.expiry, lots.id"
    rows = [dict(r) for r in c.execute(q, args)]
    c.close()
    for r in rows:
        r["eligible_tier"] = _tier(r, today)
    return rows


@app.get("/api/layers")
def layers():
    """Whole-fridge per-layer margin.

    usable: on_shelf + positive + clean (unscanned-expired clean lots count,
    they remain reachable via the consume fallback tier).
    """
    c = connect()
    today = date.today().isoformat()
    rows = [dict(r) for r in c.execute(
        """SELECT items.layer AS layer,
                  ROUND(SUM(CASE WHEN lots.status='on_shelf' AND lots.qty_remain>0
                                 AND lots.data_quality='clean'
                                 THEN lots.qty_remain ELSE 0 END),3) AS usable,
                  SUM(CASE WHEN lots.status='on_shelf' AND lots.qty_remain>0 THEN 1 ELSE 0 END) AS lots_count,
                  SUM(CASE WHEN lots.status='on_shelf' AND lots.qty_remain>0
                            AND lots.expiry IS NOT NULL AND lots.expiry<? THEN 1 ELSE 0 END) AS expired_count,
                  SUM(CASE WHEN lots.status='on_shelf' AND lots.qty_remain>0
                            AND lots.data_quality!='clean' THEN 1 ELSE 0 END) AS dirty_count
           FROM lots JOIN items ON items.id=lots.item_id
           GROUP BY items.layer ORDER BY items.layer""", (today,))]
    c.close()
    return rows


@app.get("/api/alerts")
def alerts():
    c = connect()
    warn = int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])
    today = date.today().isoformat()
    rows = [dict(r) for r in c.execute(
        """SELECT lots.*, items.name, items.layer FROM lots JOIN items ON items.id=lots.item_id
           WHERE status='on_shelf' AND expiry IS NOT NULL
             AND (qty_remain>0 OR data_quality!='clean')""")]
    c.close()
    out = []
    for r in rows:
        # Dirty lots (incl. negative quantities) surface as a data-quality
        # warning, never as "紧急" FEFO pressure, and never enter deductions.
        if r.get("data_quality", "clean") != "clean":
            r["level"] = "dirty"
            out.append(r)
            continue
        if float(r["qty_remain"]) <= 0:
            continue
        if r["expiry"] < today:
            # unscanned expired: off-shelf candidate, not an urgent-consume banner
            r["level"] = "expired_unscanned"
            out.append(r)
        elif r["expiry"] == today:
            r["level"] = "expiring_today"; r["days_left"] = 0; out.append(r)
        else:
            delta = (date.fromisoformat(r["expiry"]) - date.today()).days
            if delta <= warn:
                r["level"] = "soon"; r["days_left"] = delta; out.append(r)
    return out

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    c = connect()
    item = c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
    c.commit(); lid = cur.lastrowid; c.close(); return {"id": lid}

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    c = connect()
    try:
        return run_consume(c, body.item_id, body.qty, body.note)
    except ConsumeError as e:
        # 409 short payload carries deductions-as-planned + current state,
        # which the client renders; keep the structured body intact.
        raise HTTPException(e.status_code, e.payload)
    finally:
        c.close()

@app.post("/api/expire-sweep")
def expire_sweep():
    c = connect()
    try:
        return run_sweep(c)
    finally:
        c.close()

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
