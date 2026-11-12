"""Detect of the prassi Sources: validates the config, emits catalog items, never uses the network (P80-04).

One pipeline serves two Source types; the kind of an item is its ``meta.kind``:

* ``ade``: one item per document type x year x month page of the Agenzia delle Entrate portal (``ade:circolari:2025:07``);
* ``doctrib``: one item per estremi type x calendar month of MEF DocTrib (``doctrib:interpello:2025-10``).

The Source type is told by the config: ``tipologie`` marks the portal, ``tipoEstremi`` marks DocTrib (each is a required key with a default in its Source
type). Item key = ``<itemId>|v<version>``; the version is ``1`` and a non-blank ``refreshToken`` makes it ``1-<hash8>``. Whether a month page or a window holds
anything is found out by grab (a month without a page is counted there). The sample filters (``only``, ``maxDocs``, ``maxItems``) travel in ``meta`` and
never trim the Catalog.
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, TypeGuard
from urllib.parse import urlencode

from v11 import ProcErrorCode

MIN_YEAR = 2000
SEEN_LIMIT = 2000
MAX_ITEMS = 2000
BASE_VERSION = "1"
TIPOLOGIE = ("circolari", "risoluzioni", "provvedimenti")
TIPI_ESTREMI = ("Interpello",)
DEFAULT_ENTE = "Agenzia delle Entrate"
DEFAULT_SKIP_BEFORE = "2019-06"
SKIP_PATTERN = "^[0-9]{4}-[0-9]{2}$"
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
ADE_BASE = "https://www.agenziaentrate.gov.it/portale"
DOCTRIB_SEARCH = "https://def.finanze.it/DocTribFrontend/executeAdvancedPrassiSearch.do"
ADE_KEYS = ("tipologie", "skipBefore", "baseUrl", "maxDocs")
DOCTRIB_KEYS = ("tipoEstremi", "ente", "maxItems", "delaySeconds", "verifyTls")

_SKIP = re.compile(SKIP_PATTERN)


class DetectError(Exception):
    """A protocol error: ``code`` is one of the three v1.1 codes."""

    def __init__(self, code: ProcErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _config_error(message: str) -> DetectError:
    return DetectError(ProcErrorCode.CONFIG_ERROR, message)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def current_year() -> int:
    """The year used when a Source gives no range; a function so that tests can fix it."""
    return datetime.now(tz=UTC).year


def validate_refresh_token(token: Any) -> str | None:
    if token is None or token == "":
        return None
    if not isinstance(token, str):
        raise _config_error("refreshToken must be a string")
    return token


def validate_state(state: Any) -> dict[str, Any] | None:
    if state is None:
        return None
    seen = state.get("seen") if isinstance(state, dict) else None
    if not isinstance(state, dict) or (seen is not None and not (isinstance(seen, list) and all(isinstance(s, str) for s in seen))):
        raise _config_error("state must be an object {seen: [string]}")
    return state


def version_of(refresh_token: str | None) -> str:
    if not refresh_token:
        return BASE_VERSION
    return f"{BASE_VERSION}-{hashlib.sha256(refresh_token.encode('utf-8')).hexdigest()[:8]}"


def _with_refresh(url: str, version: str, refresh_token: str | None) -> str:
    if not refresh_token:
        return url
    return url + ("&" if "?" in url else "?") + urlencode({"refresh": version})


def resolve_source(params: dict[str, Any]) -> str:
    """``ade`` or ``doctrib``, told by the keys only one Source type has."""
    ade = [key for key in ADE_KEYS if key in params]
    doctrib = [key for key in DOCTRIB_KEYS if key in params]
    if ade and doctrib:
        msg = f"the config mixes the two Source types: {ade} belong to ade-portale, {doctrib} to doctrib-prassi"
        raise _config_error(msg)
    if not ade and not doctrib:
        raise _config_error("the Source type cannot be told: give tipologie (ade-portale) or tipoEstremi (doctrib-prassi)")
    return "ade" if ade else "doctrib"


def _years(params: dict[str, Any]) -> list[int]:
    first = params.get("annoFrom", current_year())
    last = params.get("annoTo", current_year())
    limit = current_year() + 1
    for name, value in (("annoFrom", first), ("annoTo", last)):
        if not _is_int(value) or not MIN_YEAR <= value <= limit:
            raise _config_error(f"{name} must be an integer {MIN_YEAR}..{limit}, got {value!r}")
    if first > last:
        raise _config_error(f"annoFrom ({first}) must not be after annoTo ({last})")
    return list(range(first, last + 1))


def _months(params: dict[str, Any]) -> list[int]:
    mesi = params.get("mesi")
    if mesi is None:
        return list(range(1, 13))
    if not isinstance(mesi, list) or not mesi or not all(_is_int(m) and 1 <= m <= 12 for m in mesi):  # noqa: PLR2004
        raise _config_error("mesi must be a non-empty array of months 1-12")
    return sorted(set(mesi))


def _positive(params: dict[str, Any], name: str) -> int | None:
    value = params.get(name)
    if value is None:
        return None
    if not _is_int(value) or value < 1:
        raise _config_error(f"{name} must be an integer >= 1")
    return value


def _skip_before(params: dict[str, Any]) -> tuple[int, int]:
    value = params.get("skipBefore", DEFAULT_SKIP_BEFORE)
    if not isinstance(value, str) or not _SKIP.match(value) or not 1 <= int(value[5:]) <= 12:  # noqa: PLR2004
        raise _config_error(f"skipBefore must be a month YYYY-MM, got {value!r}")
    return int(value[:4]), int(value[5:])


def _ade_only(params: dict[str, Any]) -> list[dict[str, str]] | None:
    only = params.get("only")
    if only is None:
        return None
    ok = isinstance(only, list) and all(
        isinstance(o, dict) and o.get("tipo") in TIPOLOGIE and isinstance(o.get("stem"), str) and o["stem"] for o in only
    )
    if not ok:
        raise _config_error("only must be an array of {tipo, stem} (tipo: circolari | risoluzioni | provvedimenti)")
    return list(only)


def _ade_items(params: dict[str, Any], version: str, token: str | None) -> list[dict[str, Any]]:
    tipologie = params.get("tipologie")
    if not isinstance(tipologie, list) or not tipologie or not all(t in TIPOLOGIE for t in tipologie):
        raise _config_error(f"tipologie must be a non-empty array of {list(TIPOLOGIE)}")
    years, months = _years(params), _months(params)
    skip = _skip_before(params)
    max_docs, only = _positive(params, "maxDocs"), _ade_only(params)
    items: list[dict[str, Any]] = []
    for tipologia in dict.fromkeys(tipologie):
        for anno in years:
            for mese in months:
                if (anno, mese) < skip:
                    continue
                item_id = f"ade:{tipologia}:{anno}:{mese:02d}"
                meta: dict[str, Any] = {"kind": "ade", "itemId": item_id, "version": version, "tipologia": tipologia, "anno": anno, "mese": mese}
                if max_docs is not None:
                    meta["maxDocs"] = max_docs
                if only is not None:
                    meta["only"] = only
                items.append({
                    "key": f"{item_id}|v{version}",
                    "url": _with_refresh(f"{ADE_BASE}/{MESI[mese - 1]}-{anno}-{tipologia}", version, token),
                    "meta": meta,
                })
    return items


def _doctrib_items(params: dict[str, Any], version: str, token: str | None) -> list[dict[str, Any]]:
    tipi = params.get("tipoEstremi")
    if not isinstance(tipi, list) or not tipi or not all(t in TIPI_ESTREMI for t in tipi):
        raise _config_error(f"tipoEstremi must be a non-empty array of {list(TIPI_ESTREMI)}")
    ente = params.get("ente", DEFAULT_ENTE)
    if not isinstance(ente, str) or not ente.strip():
        raise _config_error("ente must be a non-empty string")
    years, months = _years(params), _months(params)
    max_items = _positive(params, "maxItems")
    only = params.get("only")
    if only is not None and not (isinstance(only, list) and all(isinstance(n, str) and n for n in only)):
        raise _config_error("only must be an array of interpello numbers (strings)")
    items: list[dict[str, Any]] = []
    for tipo in dict.fromkeys(tipi):
        for anno in years:
            for mese in months:
                item_id = f"doctrib:{tipo.lower()}:{anno}-{mese:02d}"
                meta: dict[str, Any] = {"kind": "doctrib", "itemId": item_id, "version": version, "tipoEstremi": tipo, "ente": ente, "anno": anno, "mese": mese}
                if max_items is not None:
                    meta["maxItems"] = max_items
                if only is not None:
                    meta["only"] = only
                query = urlencode({"tipoEstremi": tipo, "ente": ente, "anno": anno, "mese": mese})
                items.append({"key": f"{item_id}|v{version}", "url": _with_refresh(f"{DOCTRIB_SEARCH}?{query}", version, token), "meta": meta})
    return items


def build_found(params: dict[str, Any], refresh_token: str | None) -> list[dict[str, Any]]:
    """Every item of the Source config, in stable order (validates the whole config)."""
    version = version_of(refresh_token)
    items = _ade_items(params, version, refresh_token) if resolve_source(params) == "ade" else _doctrib_items(params, version, refresh_token)
    if len(items) > MAX_ITEMS:
        raise _config_error(f"at most {MAX_ITEMS} items per Source, got {len(items)}: narrow the years or the months")
    return items


def build_run_result(found: list[dict[str, Any]], state_in: dict[str, Any] | None) -> dict[str, Any]:
    """Items = those whose key is not in state.seen; state keeps the newest SEEN_LIMIT keys."""
    seen = list((state_in or {}).get("seen") or [])
    known = set(seen)
    new = [item for item in found if item["key"] not in known]
    seen_out = (seen + [item["key"] for item in new])[-SEEN_LIMIT:]
    return {
        "items": new,
        "state": {"seen": seen_out},
        "metrics": {"items": len(found), "newItems": len(new)},
        "checks": [{"name": "items_found", "passed": len(found) > 0, "severity": "error"}],
    }


CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "tipologie": {"type": "array", "minItems": 1, "items": {"enum": list(TIPOLOGIE)}, "uniqueItems": True, "description": "ade-portale: document types"},
        "tipoEstremi": {"type": "array", "minItems": 1, "items": {"enum": list(TIPI_ESTREMI)}, "uniqueItems": True, "description": "doctrib-prassi: types"},
        "ente": {"type": "string"},
        "annoFrom": {"type": "integer", "minimum": MIN_YEAR},
        "annoTo": {"type": "integer", "minimum": MIN_YEAR},
        "mesi": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 12}},
        "skipBefore": {"type": "string", "pattern": SKIP_PATTERN, "description": "ade-portale: first month with a portal page (default 2019-06)"},
        "only": {"type": "array"},
        "maxDocs": {"type": "integer", "minimum": 1},
        "maxItems": {"type": "integer", "minimum": 1},
        "baseUrl": {"type": "string"},
        "delaySeconds": {"type": "number"},
        "verifyTls": {"type": "boolean"},
        "refreshToken": {"type": ["string", "null"], "description": "change it to fetch every item again"},
        "state": {"type": ["object", "null"], "properties": {"seen": {"type": "array", "items": {"type": "string"}}}},
    },
    "additionalProperties": True,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["items", "state", "metrics", "checks"],
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "required": ["key", "url", "meta"], "properties": {
            "key": {"type": "string", "minLength": 1}, "url": {"type": "string", "minLength": 1},
            "meta": {"type": "object", "required": ["kind", "itemId", "version"], "properties": {
                "kind": {"enum": ["ade", "doctrib"]}, "itemId": {"type": "string"}, "version": {"type": "string"}}}}}},
        "state": {"type": "object", "required": ["seen"], "properties": {"seen": {"type": "array", "items": {"type": "string"}}}},
        "metrics": {"type": "object", "properties": {"items": {"type": "integer"}, "newItems": {"type": "integer"}}},
        "checks": {"type": "array"},
    },
}
