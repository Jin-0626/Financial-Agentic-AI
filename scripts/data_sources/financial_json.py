"""Safe JSON output boundary for Financial Deep Agents providers.

Provider payloads can contain non-finite floats from missing prices, division by
zero, and rolling calculations. Sanitize these values before JSON serialization
so tool responses remain readable and valid across the API and report pipeline.

Usage:
    import financial_json
    financial_json.emit(result)
    payload = financial_json.dumps_bytes(result)

Optional numerical libraries are imported lazily. Sanitization does not alter
intermediate provider calculations.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal

__all__ = ["sanitize", "dumps", "dumps_bytes", "emit"]

# Optional scientific stack. Imported once, tolerated absent.
try:  # pragma: no cover - trivial
    import numpy as _np
except Exception:  # pragma: no cover - numpy is optional
    _np = None

try:  # pragma: no cover - trivial
    import pandas as _pd
except Exception:  # pragma: no cover - pandas is optional
    _pd = None


def _finite_or_none(value):
    """float -> the float, or None when it is NaN / +-Infinity."""
    return None if (math.isnan(value) or math.isinf(value)) else value


def sanitize(obj):
    """Recursively replace every non-finite float in `obj` with ``None``.

    Containers are rebuilt, not mutated, so the caller's data is untouched.
    Handles the types that actually reach our output boundaries:

    * ``float`` and ``numpy.float64`` (which subclasses ``float``)
    * ``numpy.float32`` / ``float16`` — these do **NOT** subclass ``float``, so
      an ``isinstance(obj, float)`` check alone silently lets ``nan`` through
      to ``default=str`` and it lands in the JSON as the *string* ``"nan"``.
    * ``numpy.ndarray`` — not a list/tuple, so it is easy to miss entirely.
    * ``pandas`` NA / NaT scalars.
    * dict keys, which must end up as JSON strings.
    """
    # --- scalars ------------------------------------------------------------
    if obj is None or isinstance(obj, (str, bool, int)):
        # bool before int on purpose (bool IS an int); both are always finite.
        return obj

    if isinstance(obj, float):  # covers numpy.float64
        return _finite_or_none(obj)

    if isinstance(obj, Decimal):
        return None if not obj.is_finite() else float(obj)

    if _np is not None:
        if isinstance(obj, _np.ndarray):
            return [sanitize(v) for v in obj.tolist()]
        if isinstance(obj, _np.floating):  # float16/32/128 — NOT a python float
            return _finite_or_none(float(obj))
        if isinstance(obj, _np.integer):
            return int(obj)
        if isinstance(obj, _np.bool_):
            return bool(obj)

    if _pd is not None:
        # NaT and pd.NA are not floats and would otherwise stringify.
        if obj is getattr(_pd, "NaT", None) or obj is getattr(_pd, "NA", None):
            return None
        if isinstance(obj, _pd.Series):
            return [sanitize(v) for v in obj.tolist()]
        if isinstance(obj, _pd.DataFrame):
            return sanitize(obj.to_dict(orient="records"))

    # --- containers ---------------------------------------------------------
    if isinstance(obj, dict):
        # JSON object keys must be strings; a NaN key would be nonsense anyway.
        return {(k if isinstance(k, str) else str(k)): sanitize(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set, frozenset)):
        return [sanitize(v) for v in obj]

    # Anything else (datetime, custom objects) is left for `default=str`.
    return obj


def dumps(obj, **kwargs) -> str:
    """``json.dumps`` that can never emit invalid JSON.

    Extra kwargs are forwarded, except ``allow_nan`` and ``default`` which are
    fixed. Do NOT pass ``indent``: the host's stdout parser wants the JSON
    envelope on one line (see the note in yfinance_data.py::main).
    """
    kwargs.pop("allow_nan", None)
    kwargs.setdefault("default", str)
    kwargs.setdefault("ensure_ascii", False)
    return json.dumps(sanitize(obj), allow_nan=False, **kwargs)


def dumps_bytes(obj, **kwargs) -> bytes:
    """UTF-8 encoded :func:`dumps` — for length-prefixed frame protocols."""
    return dumps(obj, **kwargs).encode("utf-8")


def emit(obj, **kwargs) -> str:
    """``print(dumps(obj))`` and return what was printed."""
    text = dumps(obj, **kwargs)
    print(text)
    return text
