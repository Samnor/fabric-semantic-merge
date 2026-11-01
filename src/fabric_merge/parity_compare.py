"""Explicit, deterministic numeric comparison without greedy row matching."""

from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import json
import math

from .util import encoded, require


def numeric_policy(value):
    if value is None:
        return None
    require(isinstance(value, dict), "Comparison must be an object")
    require(
        set(value)
        <= {"mode", "key_columns", "absolute_tolerance", "relative_tolerance"},
        "Unknown comparison option",
    )
    require(
        value.get("mode") == "keyed_numeric", "Comparison mode must be keyed_numeric"
    )
    keys = value.get("key_columns")
    require(
        isinstance(keys, list)
        and all(isinstance(k, str) and k for k in keys)
        and len(set(keys)) == len(keys),
        "Provide unique key_columns; [] explicitly selects a one-row scalar comparison",
    )
    policy = {"mode": "keyed_numeric", "key_columns": list(keys)}
    for field in ("absolute_tolerance", "relative_tolerance"):
        raw = value.get(field, 0)
        require(
            type(raw) in (str, int, float) and len(str(raw)) <= 100, "Invalid " + field
        )
        try:
            number = Decimal(str(raw))
        except InvalidOperation as exc:
            raise ValueError("Invalid " + field) from exc
        require(
            number.is_finite()
            and number >= 0
            and abs(number.as_tuple().exponent) <= 308,
            "Tolerance must be finite, nonnegative and within decimal exponent +/-308",
        )
        policy[field] = str(number)
    return policy


def _number(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def _text(value):
    # Inputs are finite decimal rationals; sufficient precision gives a finite
    # decimal diagnostic. Decisions below use exact Fraction arithmetic.
    with localcontext() as context:
        context.prec = len(str(abs(value.numerator))) + len(str(value.denominator)) + 2
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def compare_numeric(source, target, configuration):
    policy = numeric_policy(configuration)
    keys = policy["key_columns"]
    if not keys:
        require(
            len(source) == len(target) == 1,
            "Empty key_columns requires exactly one row on each side",
        )

    def indexed(rows):
        result = {}
        for row in rows:
            require(
                all(k in row for k in keys),
                "Comparison key column missing from a result row",
            )
            identity = encoded({k: row[k] for k in keys})
            require(
                identity not in result,
                "Comparison keys are not unique; use exact comparison or choose unique keys",
            )
            result[identity] = row
        return result

    left, right = indexed(source), indexed(target)
    abs_tol = Fraction(policy["absolute_tolerance"])
    rel_tol = Fraction(policy["relative_tolerance"])
    differences, numeric_cells, tolerated_cells = [], 0, 0
    total = 0

    def difference(value):
        nonlocal total
        total += 1
        if len(differences) < 10:
            differences.append(value)

    for identity in sorted(left.keys() & right.keys()):
        a, b = left[identity], right[identity]
        for column in sorted((a.keys() | b.keys()) - set(keys)):
            if column not in a or column not in b:
                difference(
                    {
                        "key": json_key(identity),
                        "column": column,
                        "kind": "missing_column",
                        "source_present": column in a,
                        "target_present": column in b,
                    }
                )
                continue
            x, y = a[column], b[column]
            if _number(x) and _number(y):
                numeric_cells += 1
                x_value, y_value = Fraction(str(x)), Fraction(str(y))
                delta = abs(x_value - y_value)
                allowed = max(abs_tol, rel_tol * max(abs(x_value), abs(y_value)))
                if delta > allowed:
                    difference(
                        {
                            "key": json_key(identity),
                            "column": column,
                            "kind": "numeric",
                            "source": x,
                            "target": y,
                            "absolute_difference": _text(delta),
                            "allowed_difference": _text(allowed),
                        }
                    )
                elif encoded(x) != encoded(y):
                    tolerated_cells += 1
            elif encoded(x) != encoded(y):
                difference(
                    {
                        "key": json_key(identity),
                        "column": column,
                        "kind": "exact",
                        "source": x,
                        "target": y,
                    }
                )
    missing, extra = left.keys() - right.keys(), right.keys() - left.keys()
    return {
        "equal": not missing and not extra and total == 0,
        "numeric_comparison": {
            "policy": policy,
            "numeric_cells": numeric_cells,
            "tolerated_cells": tolerated_cells,
            "cell_mismatches": total,
            "missing_keys": len(missing),
            "unexpected_keys": len(extra),
        },
        "cell_differences": {
            "count": total,
            "samples": differences,
            "samples_truncated": total > 10,
        },
    }


def json_key(value):
    return json.loads(value)
