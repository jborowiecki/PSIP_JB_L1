"""Niezależne przykłady rachunkowe, błędy danych i granice modelu."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from python.passability_model import calculate, sensitivity, ranks


class PassabilityTests(unittest.TestCase):
    def test_county_maxima_and_zero_weight_in_denominator(self):
        result = calculate([[10, 100, 5, 0], [5, 0, 10, 0]], [1, -.8, 0, -.6])
        self.assertEqual(result['maxima'], [10, 100, 10, 0])
        self.assertEqual(result['denominator'], 3)
        self.assertEqual(result['normalized'][1], [.5, 0, 1, 0])
        self.assertAlmostEqual(result['scores'][0], 1/15)
        self.assertAlmostEqual(result['scores'][1], 1/6)

    def test_maxima_are_not_shared_between_resolutions(self):
        self.assertEqual(calculate([[10], [5]], [1])['scores'], [1, .5])
        self.assertEqual(calculate([[5], [1]], [1])['scores'], [1, .2])

    def test_signed_weights_and_real_attainable_bounds(self):
        model = calculate([[10, 0, 0], [0, 10, 10]], [1, -.8, -.6])
        self.assertAlmostEqual(model['scores'][0], 1/3)
        self.assertAlmostEqual(model['scores'][1], -1.4/3)

    def test_invalid_inputs_do_not_silently_become_zero(self):
        for rows, weights in [([[1]], [1.1]), ([[1]], [float('nan')]),
                              ([[-1]], [1]), ([[None]], [1]), ([[float('inf')]], [1]),
                              ([[0]], [1]), ([], [1]), ([[1, 2]], [1])]:
            with self.subTest(rows=rows, weights=weights), self.assertRaises(ValueError):
                calculate(rows, weights)

    def test_sensitivity_clamps_weights_and_preserves_zero_weights(self):
        model = calculate([[10, 10, 10], [5, 10, 5]], [1, -.8, 0])
        report = sensitivity(model, [1, -.8, 0])
        self.assertEqual(len(report['scenarios']), 4)
        upper_road = next(s for s in report['scenarios'] if s['index'] == 0 and s['factor'] > 1)
        self.assertTrue(upper_road['clipped'])
        self.assertFalse(upper_road['changed'])
        self.assertAlmostEqual(report['low'][0], (.8-.96)/3)
        self.assertAlmostEqual(report['high'][0], (1-.64)/3)
        self.assertFalse(report['stable'][0])
        for lo, score, hi in zip(report['low'], model['scores'], report['high']):
            self.assertLessEqual(lo, score)
            self.assertLessEqual(score, hi)

    def test_tied_ranks(self):
        self.assertEqual(ranks([3, 1, 1, 2]), [4, 1.5, 1.5, 3])


if __name__ == '__main__':
    unittest.main()
