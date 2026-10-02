"""Minimal local stand-in for Databricks' dlt module: runs table functions in batch mode."""
import functools

_TABLES = {}       # name -> function
_ORDER = []
_RESULTS = {}      # name -> DataFrame
_EXPECT = {}       # name -> list of SQL conditions (expect_or_drop)


def table(name=None, **_):
    def deco(fn):
        n = name or fn.__name__
        _TABLES[n] = fn
        _ORDER.append(n)
        return fn
    return deco


view = table


def expect_or_drop(_name, cond):
    def deco(fn):
        _EXPECT.setdefault(fn.__name__, []).append(cond)
        return fn
    return deco


def expect(_name, _cond):
    return lambda fn: fn


def read(name):
    return _RESULTS[name]


read_stream = read


def run(overrides):
    """Evaluate tables in definition order; ``overrides`` replaces tables (e.g. bronze)."""
    for n in _ORDER:
        df = overrides[n]() if n in overrides else _TABLES[n]()
        for cond in _EXPECT.get(_TABLES[n].__name__, []):
            df = df.filter(cond)
        _RESULTS[n] = df.cache()
    return _RESULTS
