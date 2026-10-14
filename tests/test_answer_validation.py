"""Regression checks for answer provenance, quantity notation and stable citations."""
import unittest
from decimal import Decimal

from backend.answer_validation import grounded_sections, numbers, quantities, render_answer, review_items
from backend.wiki_store import WikiError


def statement(text, *, heading='논문 근거', evidence_ids=None, quotes=None):
    return {'sections': [{'heading': heading, 'statements': [{'kind': 'finding', 'text': text,
        'evidence_ids': evidence_ids or ['claim:c'], 'quotes': quotes or {}}]}]}


def claim_evidence(text):
    return {'claim:c': {'kind': 'claim', 'claim': {'id': 'claim-c', 'summary': text, 'scope': ''}}}


class AnswerValidationTests(unittest.TestCase):
    def test_scientific_notation_equivalents_preserve_value_and_dimension(self):
        source = 'Measured current was 1.20e-3 A.'
        for display in ('1.20e-3 A', '1.20 × 10⁻³ A', '1.20 x 10^{-3} A', '0.00120 A'):
            with self.subTest(display=display):
                checked = grounded_sections(statement('Measured current was '+display+'.'), claim_evidence(source))
                self.assertTrue(checked)
                self.assertEqual(numbers(display), {Decimal('0.00120')})
                self.assertEqual(quantities(display), {('current', Decimal('0.00120'))})
        self.assertEqual(quantities('10⁻⁹ s'), {('time', Decimal('1e-9'))})

    def test_same_number_with_wrong_time_or_voltage_unit_is_rejected(self):
        for source, output in (('Retention was 100 s.', 'Retention was 100 ms.'),
                               ('Pulse amplitude was 100 mV.', 'Pulse amplitude was 100 V.'),
                               ('Delay was 100 ms.', 'Amplitude was 100 mV.')):
            with self.subTest(output=output), self.assertRaises(WikiError):
                grounded_sections(statement(output), claim_evidence(source))

    def test_supported_unit_aliases_are_accepted(self):
        for source, outputs in (
            ('Reduction was 74 percent.', ['Reduction was 74%.']),
            ('Retention was 100 seconds.', ['Retention was 100 s.', 'Retention was 100 sec.']),
            ('Pulse duration was 10 µs.', ['Pulse duration was 10 μs.', 'Pulse duration was 10 us.']),
            ('Temperature was 90 C.', ['Temperature was 90 °C.', 'Temperature was 90 ℃.']),
            ('Length was 3 µm.', ['Length was 3 μm.']),
        ):
            for output in outputs:
                with self.subTest(output=output):
                    self.assertTrue(grounded_sections(statement(output), claim_evidence(source)))

    def test_unverified_heading_is_neutralized_before_rendering_and_review(self):
        bad_heading = 'This proves retention lasts 999999 years'
        result = grounded_sections(statement('The result has a limited scope.', heading=bad_heading),
                                   claim_evidence('The result has a limited scope.'))
        self.assertEqual(result[0]['heading'], '근거와 해석')
        self.assertNotIn(bad_heading, render_answer(result, claim_evidence('The result has a limited scope.')))
        items = review_items({'synthesis': result})
        self.assertEqual(items[0]['section_heading'], '근거와 해석')
        allowed = grounded_sections(statement('The result has a limited scope.', heading='적용 한계'),
                                    claim_evidence('The result has a limited scope.'))
        self.assertEqual(allowed[0]['heading'], '적용 한계')

    def test_citations_pin_claim_run_wiki_revision_and_source_page(self):
        evidence = claim_evidence('The result requires context.')
        evidence['wiki:w'] = {'kind': 'wiki', 'id': 'topic with space', 'title': 'Context',
                              'revision': 7, 'support_ids': ['claim:c']}
        quote = 'The measured result requires context.'
        evidence['source:s'] = {'kind': 'source_text', 'paper_id': 'paper-one', 'page_id': 'page-one',
                                'document_id': 'doc-one', 'page_number': 4, 'offset': 0, 'text': quote}
        sections = grounded_sections(statement('The result requires context.',
            evidence_ids=['claim:c', 'wiki:w', 'source:s'], quotes={'source:s': quote}), evidence)
        rendered = render_answer(sections, evidence, run_id='run-123')
        self.assertIn('/api/pilot/answer-runs/run-123/claims/claim-c', rendered)
        self.assertNotIn('](/api/pilot/claims/', rendered)
        self.assertIn('/api/pilot/wiki-files/topic%20with%20space/markdown?revision=7', rendered)
        self.assertIn('/api/pilot/documents/doc-one/pdf#page=4', rendered)
        self.assertIn('Context · v7', rendered)
        self.assertIn('> '+quote, rendered)

    def test_malformed_nested_statements_raise_controlled_validation_errors(self):
        valid_statement = statement('The result requires context.')['sections'][0]['statements'][0]
        malformed = [
            {'sections': [None]}, {'sections': [{'heading': [], 'statements': [valid_statement]}]},
            {'sections': [{'heading': '논문 근거', 'statements': [None]}]},
            {'sections': [{'heading': '논문 근거', 'statements': [{**valid_statement, 'evidence_ids': [[]]}]}]},
            {'sections': [{'heading': '논문 근거', 'statements': [{**valid_statement, 'quotes': []}]}]},
            {'sections': [{'heading': '논문 근거', 'statements': [{**valid_statement, 'kind': {}}]}]},
        ]
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(WikiError):
                grounded_sections(value, claim_evidence('The result requires context.'))


if __name__ == '__main__':
    unittest.main()
