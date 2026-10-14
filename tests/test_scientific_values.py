import json
import unittest

from backend.scientific_values import validate_scientific_value
from backend.pilot_curate import apply_curation
from backend.pilot_db import connect
import test_pilot as fixtures


class ScientificValueTests(unittest.TestCase):
    def test_explicit_notations_preserve_numeric_meaning(self):
        for raw in ("1.20e-9 A", "1.20 × 10^-9 A", "1.20×10^{-9} A", "1.20 × 10⁻⁹ A"):
            validate_scientific_value(raw, 1.2e-9)
        validate_scientific_value(">10^12 cycles", 1e12)
        validate_scientific_value("10 2 cycles", None)
        validate_scientific_value("1.8–2.6×", None)

    def test_lost_exponent_numeric_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_scientific_value("10^2 cycles", 102)
        with self.assertRaises(ValueError):
            validate_scientific_value("<1.2×10^-18 A", 1.2e18)

    def test_invalid_scientific_curation_rolls_back(self):
        fixture=fixtures.PilotTests();fixture.setUp()
        try:
            path,payload=fixture.curation()
            payload['papers'][0]['claims'][0]['measurements'][0].update(raw_value='10^2 cycles',numeric_value=102)
            path.write_text(json.dumps(payload),encoding='utf-8')
            with self.assertRaises(ValueError):
                apply_curation(path,fixture.db)
            with connect(fixture.db) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0],0)
        finally:
            fixture.tearDown()
