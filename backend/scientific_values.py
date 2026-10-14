"""Validate explicit scientific scalar notation without guessing lost exponents."""
import math
import re
from decimal import Decimal

SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
NUMBER = r"[+−-]?(?:\d+(?:\.\d*)?|\.\d+)"
SCIENTIFIC = re.compile(r"^(?:[<>≤≥≈~]=?\s*)?(?:(?P<e>" + NUMBER + r")[eE](?P<ep>[+−-]?\d{1,4})|(?:(?P<c>" + NUMBER + r")\s*[×x*·]\s*)?10(?:\^\s*(?:\{(?P<b>[+−-]?\d{1,4})\}|(?P<p>[+−-]?\d{1,4}))|(?P<u>[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)))(?P<unit>\s+[^\d].*|$)")


def validate_scientific_value(raw_value, numeric_value):
    match = SCIENTIFIC.fullmatch(raw_value.strip())
    if not match:
        return  # Ranges or ambiguous extracted glyphs require source review.
    power = (match['ep'] or match['b'] or match['p'] or match['u'].translate(SUPERSCRIPTS)).replace('−', '-')
    if abs(int(power)) > 308:
        raise ValueError("Scientific exponent exceeds the supported numeric range")
    mantissa = (match['e'] or match['c'] or '1').replace('−', '-')
    expected = float(Decimal(mantissa) * (Decimal(10) ** int(power)))
    if numeric_value is None or not math.isfinite(float(numeric_value)) or not math.isfinite(expected) or not math.isclose(float(numeric_value), expected, rel_tol=1e-12, abs_tol=0):
        raise ValueError("Scientific raw_value and numeric_value disagree; verify the source exponent")
