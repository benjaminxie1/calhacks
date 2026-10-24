"""Mock source services (R2): one API per system, each with its own key.

The same functions back two transports: FastAPI routes (HTTP, for the demo and
the Sources page) and an in-process client (for fast replay). Both check the key.
"""
from __future__ import annotations

import hashlib
from datetime import date

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException

from bp.sources.store import SourceStore, get_store

SOURCES = ["oms", "executions", "booking", "custodian", "bank", "reference", "fees", "directory"]
KEYS = {s: "bpk_" + s + "_" + hashlib.sha256(f"breakpoint:{s}".encode()).hexdigest()[:20] for s in SOURCES}


class KeyRejected(PermissionError):
    pass


def authorize(source: str, key: str | None) -> None:
    if key != KEYS[source]:
        raise KeyRejected(f"{source}: key rejected")


def _j(models):
    return [m.model_dump(mode="json") for m in models]


def handlers(st: SourceStore) -> dict:
    """source -> {endpoint: fn(d) -> json}"""
    return {
        "oms": {"orders": lambda d: _j(st.oms_orders(d))},
        "executions": {"executions": lambda d: _j(st.broker_executions(d))},
        "booking": {"txns": lambda d: _j(st.booking_feed(d))},
        "custodian": {
            "settlements": lambda d: _j(st.custodian_settlements(d)),
            "positions": lambda d: [{"account_id": a, "symbol": s, "qty": str(q)}
                                    for (a, s), q in sorted(st.custodian_positions(d).items())],
            "cash": lambda d: [{"account_id": a, "balance": str(b)} for a, b in sorted(st.custodian_cash(d).items())],
            "activity": lambda d: _j(st.custodian_cash_activity(d)),
        },
        "bank": {"transfers": lambda d: _j(st.bank_transfers_asof(d))},
        "reference": {
            "prices": lambda d: [{"symbol": s.symbol, "close_px": str(st.price(d, s.symbol))} for s in st.symbols],
            "corporate_actions": lambda d: _j(st.corporate_actions_asof(d)),
            "symbols": lambda d: _j(st.symbols),
        },
        "fees": {"schedule": lambda d: st.fee_schedule_on(d).model_dump(mode="json"),
                 "accounts": lambda d: _j(st.accounts)},
        "directory": {"people": lambda d: _j(st.people)},
    }


class SourceClient:
    """In-process client for one source. Behaves like the HTTP API, key check included."""

    def __init__(self, source: str, key: str, store: SourceStore | None = None):
        self.source, self.key = source, key
        self.store = store or get_store()

    def get(self, endpoint: str, d: date):
        authorize(self.source, self.key)
        return handlers(self.store)[self.source][endpoint](d)


def make_app(store: SourceStore | None = None) -> FastAPI:
    app = FastAPI(title="Meridian source systems (mock)")
    st = store or get_store()
    hs = handlers(st)
    for source, eps in hs.items():
        router = APIRouter(prefix=f"/{source}", tags=[source])

        def dep(x_api_key: str | None = Header(default=None), _s=source):
            try:
                authorize(_s, x_api_key)
            except KeyRejected as e:
                raise HTTPException(status_code=401, detail=str(e)) from None

        for ep, fn in eps.items():
            def route(d: date, _fn=fn):
                return _fn(d)
            router.add_api_route(f"/{ep}", route, methods=["GET"], dependencies=[Depends(dep)], name=f"{source}_{ep}")
        app.include_router(router)
    return app
