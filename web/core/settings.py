DEFAULTS = {"block_mb": 5, "transfers": 3, "kdf": 17}
LIMITS = {"block_mb": (0.25, 5), "transfers": (1, 32), "kdf": (15, 18)}
INTEGER = {"transfers", "kdf"}


def clean(values, base=None):
    result = dict(base or DEFAULTS)
    for key, (low, high) in LIMITS.items():
        try:
            raw = float((values or {}).get(key, result[key]))
            if raw != raw:
                continue
            value = int(round(raw)) if key in INTEGER else round(raw, 2)
        except (TypeError, ValueError, OverflowError):
            continue
        result[key] = max(low, min(high, value))
    return result
