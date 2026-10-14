"""Typography tolerance must not turn scientific changes into valid quotations."""
import json
import unittest
from unittest.mock import patch

from backend.quote_matching import locate_quote, QuoteMatchError
from backend.wiki_engine import validate_sections
from backend.wiki_lint import validate_lint_finding, validate_grounding
from backend.wiki_store import WikiCitationError
from backend.answer_validation import grounded_sections


def evidence(text, **kwargs):
    return dict(kind='source_text', text=text, paper_id='p1', document_id='d1',
                page_id='page1', page_number=1, offset=0, **kwargs)


def result(quote, key='a', text='The reported result retains its conditions.'):
    return {'sections': [{'heading': '논문 근거', 'statements': [dict(
        text=text, kind='finding', evidence_ids=[key], quotes={key: quote})]}]}


class QuoteMatchingTests(unittest.TestCase):
    def assert_match(self, raw, quote):
        text = 'prefix\n'+raw+'\ntrailer'
        start, end = locate_quote(text, quote)
        self.assertEqual((start, end), (7, 7+len(raw)))
        self.assertEqual(text[start:end], raw)

    def assert_rejected(self, raw, quote, code='quote_not_found'):
        with self.assertRaises(QuoteMatchError) as found:
            locate_quote(raw, quote)
        self.assertEqual(found.exception.code, code)

    def test_whitespace_matching_still_preserves_raw_offsets(self):
        for space in (' ', '\n', '\t', '\u00a0', '\u2009', '\r\n'):
            with self.subTest(space=repr(space)):
                self.assert_match('retention'+space+'exceeds 100 s', 'retention exceeds 100 s')

    def test_mixed_line_wraps_retain_or_drop_each_hyphen_independently(self):
        self.assert_match('The require-\nment uses a thin-\nfilm layer > 100 mA.',
                          'The requirement uses a thin-film layer > 100mA.')
        self.assert_match('The require‑\r\n  ment uses 100 mA.', 'The requirement uses 100 mA.')

    def test_inline_compound_hyphens_are_never_removed(self):
        for hyphen in ('-', '‐', '‑'):
            self.assert_rejected('A thin'+hyphen+'film transistor.', 'A thinfilm transistor.')
        self.assert_rejected('A require- ment exists.', 'A requirement exists.')

    def test_only_word_hyphen_glyphs_are_equated(self):
        for hyphen in ('‐', '‑'):
            self.assert_match('A thin'+hyphen+'film transistor.', 'A thin-film transistor.')
        for dash in ('−', '–', '—'):
            self.assert_rejected('A thin'+dash+'film transistor.', 'A thin-film transistor.')

    def test_ligatures_canonical_accents_and_apostrophes(self):
        self.assert_match('The efﬁciency of the device’s café sensor.', "The efficiency of the device's cafe\u0301 sensor.")
        self.assert_match('“A ﬂoating gate”', '"A floating gate"')
        self.assert_match('cafe\u0301 efficiency', 'café efficiency')
        self.assert_match('A q\u0307\u0323 layer.', 'A q\u0323\u0307 layer.')

    def test_invisible_characters_only_disappear_inside_words(self):
        for invisible in ('\u00ad', '\u200b', '\ufeff'):
            self.assert_match('The require'+invisible+'ment is strict.', 'The requirement is strict.')
            self.assert_rejected('The value is 1'+invisible+'00 s.', 'The value is 100 s.')

    def test_number_unit_spacing_without_unit_conversion(self):
        for raw, quote in [('Measured 25 % at 10 mV.', 'Measured 25% at 10mV.'),
                           ('Measured 25% at 10mV.', 'Measured 25 % at 10 mV.')]:
            self.assert_match(raw, quote)

    def test_numbers_signs_ranges_inequalities_and_units_stay_strict(self):
        pairs = [
            ('Current > 100 mA.', 'Current > 101 mA.'),
            ('Current > 100 mA.', 'Current < 100 mA.'),
            ('Current > 100 mA.', 'Current ≥ 100 mA.'),
            ('Current > 100 mA.', 'Current = 100 mA.'),
            ('Current -100 mA.', 'Current 100 mA.'),
            ('Current −100 mA.', 'Current -100 mA.'),
            ('Current 100 mA.', 'Current 100 A.'),
            ('Current 100 mA.', 'Current 100 MA.'),
            ('Current 100 µA.', 'Current 100 μA.'),
            ('Current 100 µA.', 'Current 100 uA.'),
            ('Value 10² s.', 'Value 102 s.'),
            ('Value 10² s.', 'Value 10^2 s.'),
            ('Value 1e-3 s.', 'Value 1e3 s.'),
            ('Value 1.20 s.', 'Value 1.2 s.'),
            ('Value 1 00 s.', 'Value 100 s.'),
            ('Value 1–2 s.', 'Value 1-2 s.'),
            ('Value 10-\n20 s.', 'Value 1020 s.'),
            ('Value ms-\nns.', 'Value msns.'),
            ('A H₂ layer.', 'A H2 layer.'),
            ('Value 100 ℃.', 'Value 100 °C.'),
        ]
        for raw, quote in pairs:
            with self.subTest(raw=raw, quote=quote):
                self.assert_rejected(raw, quote)

    def test_omissions_ocr_repairs_and_paraphrases_stay_rejected(self):
        for raw, quote in [('Retention [12] improved.', 'Retention improved.'),
                           ('Retention increased.', 'Retention improved.'),
                           ('The value is 1O0 s.', 'The value is 100 s.'),
                           ('Result one. Intervening text. Result two.', 'Result one. Result two.')]:
            self.assert_rejected(raw, quote)

    def test_partial_number_or_unit_tokens_cannot_form_a_quote(self):
        self.assertEqual(locate_quote('Current was 100.', 'Current was 100'), (0, 15))
        for raw, quote in [('1100 mA at 85 C.', '100 mA at 85 C.'),
                           ('-100 mA at 85 C.', '100 mA at 85 C.'),
                           ('1.100 mA at 85 C.', '100 mA at 85 C.'),
                           ('Current was 100.', 'Current was 10'),
                           ('Current was 10.5 mA.', 'Current was 10'),
                           ('Current was 100 mAs.', 'Current was 100 mA')]:
            self.assert_rejected(raw, quote)

    def test_ambiguous_and_missing_are_distinct(self):
        self.assert_rejected('same same', 'same', 'quote_ambiguous')
        self.assert_rejected('thin‑film; thin‐film', 'thin-film', 'quote_ambiguous')
        self.assert_rejected('same', 'different')
        self.assert_rejected('same', '  ', 'quote_empty')
        self.assert_rejected('same', None, 'quote_invalid')

    def test_literal_match_takes_priority_over_typography_fallback(self):
        self.assertEqual(locate_quote('thin-film then thin‑film', 'thin-film'), (0, 9))

    def test_expanded_ligature_cannot_be_partially_cited(self):
        self.assert_rejected('ﬃ', 'fi')
        self.assertEqual(locate_quote('ﬃ', 'ffi'), (0, 1))

    def test_wiki_keeps_original_quote_and_absolute_page_offset(self):
        raw = 'The require-\nment of thin‑film devices exceeds 100 mA.'
        ev = evidence(raw); ev['offset'] = 37
        sections = validate_sections(result('The requirement of thin-film devices exceeds 100mA.'), {'a': ev})
        span = sections[0]['statements'][0]['source_spans'][0]
        self.assertEqual(span['quote'], raw)
        self.assertEqual((span['start_offset'], span['end_offset']), (37, 37+len(raw)))

    def test_wiki_reason_codes_cover_id_boundary_length_and_ambiguity(self):
        ev = {'a': evidence('The retention improved significantly.')}
        cases = [
            (ev, 'Unknown retention experiment.', 'quote_not_found'),
            (ev, 'retention', 'quote_too_short'),
            (ev, '', 'quote_empty'),
            ({'a': evidence('retention improved; retention improved')}, 'retention improved', 'quote_ambiguous'),
            ({'a': evidence('Leakage was reduced.'), 'b': evidence('The retention improved significantly.')},
             'The retention improved significantly.', 'quote_wrong_evidence'),
            ({'a': evidence('The retention'), 'b': {**evidence('improved significantly.'), 'page_number': 2}},
             'The retention improved significantly.', 'quote_boundary'),
            ({'a': evidence('The retention '), 'b': {**evidence('improved significantly.'), 'offset': 14}},
             'The retention improved significantly.', 'quote_boundary'),
        ]
        for items, quote, code in cases:
            with self.subTest(code=code), self.assertRaises(WikiCitationError) as found:
                validate_sections(result(quote), items)
            self.assertEqual(found.exception.code, code)
            self.assertIn('p.1', str(found.exception))
            self.assertIn('근거 a', str(found.exception))

    def test_boundary_diagnosis_never_joins_different_documents_or_missing_pages(self):
        for update in ({'document_id': 'other'}, {'page_number': 3}):
            ev = {'a': evidence('The retention'), 'b': {**evidence('improved significantly.'), 'page_number': 2, **update}}
            with self.assertRaises(WikiCitationError) as found:
                validate_sections(result('The retention improved significantly.'), ev)
            self.assertEqual(found.exception.code, 'quote_not_found')

    def test_answer_validation_reuses_matching_but_still_checks_numeric_claims(self):
        raw = 'The thin‑film current was 100 mA.'
        ev = {'a': evidence(raw)}
        valid = grounded_sections(result('The thin-film current was 100mA.', text='Current was 100 mA.'), ev)
        self.assertEqual(valid[0]['statements'][0]['source_spans'][0]['quote'], raw)
        for text in ('Current was 101 mA.', 'Current was 100 V.'):
            with self.assertRaises(ValueError):
                grounded_sections(result('The thin-film current was 100mA.', text=text), ev)
        with self.assertRaises(WikiCitationError):
            grounded_sections(result('The thin-film current was 100 A.'), ev)

    def test_lint_uses_same_matcher_and_saves_raw_quote_and_global_offsets(self):
        raw = 'The thin‑film require-\nment exceeds 100 mA.'
        item = dict(severity='warning', quote='The thin-film requirement exceeds 100mA.', issue='조건 확인', suggestion='검토')
        validated = validate_lint_finding({**item, 'page_id': 'invented'}, raw, 14000)
        self.assertEqual(validated['quote'], raw)
        self.assertEqual((validated['start_offset'], validated['end_offset']), (14000, 14000+len(raw)))
        self.assertNotIn('page_id', validated)
        for quote in ('The thin-film requirement exceeds 100 A.', 'The thin-film requirement exceeds 101 mA.'):
            with self.assertRaises(WikiCitationError):
                validate_lint_finding({**item, 'quote': quote}, raw)
        with self.assertRaises(WikiCitationError) as found:
            validate_lint_finding({**item, 'quote': 'thin-film'}, 'thin‑film thin‐film')
        self.assertEqual(found.exception.code, 'quote_ambiguous')

    def test_lint_stored_provenance_is_exact_even_when_typography_matches(self):
        raw = 'The thin‑film current was 100 mA.'
        span = dict(page_id='page1', start_offset=0, end_offset=len(raw), quote=raw)
        refs = dict(paper_ids=['p1'], claim_ids=[], dependencies={}, source_spans=[span])
        state = {'papers': {'p1': dict(claims=[], documents=[], pages=[dict(id='page1', text=raw)])}}
        def problems():
            return validate_grounding({'refs_json': json.dumps(refs)}, '', state, {}, None)
        with patch('backend.wiki_lint.current', return_value=True):
            self.assertEqual(problems(), [])
            span['quote'] = raw.replace('‑', '-')
            self.assertIn('quote_source_changed', problems()[0])
            span.update(quote=raw, end_offset=len(raw)+1)
            self.assertIn('quote_source_changed', problems()[0])


if __name__ == '__main__':
    unittest.main()
