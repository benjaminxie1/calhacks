"""Mock source services (R2): one API per system, each with its own key.

The same functions back two transports: FastAPI routes (HTTP, for the demo and
the Sources page) and an in-process client (for fast replay). Both check the key.
"""
from __future__ import annotations

import hashlib
from datetime import date

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException

from bp.sources.store import SourceStore, get_store

SOURCES = ["leases", "statements", "ap", "bank", "county", "reference", "booking", "directory"]
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
        "leases": {"abstracts": lambda d: _j(st.leases)},
        "statements": {"received": lambda d: _j(st.statements_received(d)),
                       "asof": lambda d: _j(st.statements_asof(d))},
        "ap": {"payments": lambda d: _j([p for p in st.payments if p.paid_on <= d])},
        "bank": {"items": lambda d: _j([b for b in st.bank if b.status_date <= d])},
        "county": {"tax_records": lambda d: _j(st.tax_records)},
        "reference": {"centers": lambda d: _j(st.centers), "landlords": lambda d: _j(st.landlords)},
        "booking": {"txns": lambda d: _j(st.booking_feed(d))},
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
    app = FastAPI(title="Golden Wok source systems (mock)")
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
