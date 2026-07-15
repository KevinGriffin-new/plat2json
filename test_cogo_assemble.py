#!/usr/bin/env python3
"""Focused regression tests for COGO link-traverse validation."""
import unittest

import cogo_assemble


# Page coordinates are y-down, so north is negative y.
NODES = [(0.0, 0.0), (100.0, 0.0), (100.0, -100.0)]
EDGES = [
    {"a": 0, "b": 1, "chain": 0, "t0": 0.0, "t1": 100.0},
    {"a": 1, "b": 2, "chain": 1, "t0": 0.0, "t1": 100.0},
]
LINK = {
    "id": "corner",
    "start_EN": [0.0, 0.0],
    "end_EN": [100.0, 100.0],
    "segs": [{"edge": 0, "forward": True}, {"edge": 1, "forward": True}],
}


class LinkTraverseTests(unittest.TestCase):
    def test_exact_link_closes_to_known_control(self):
        courses = [
            {"seg": 0, "azimuth": 90.0, "distance": 100.0, "flags": []},
            {"seg": 1, "azimuth": 0.0, "distance": 100.0, "flags": []},
        ]
        report = cogo_assemble.validate_links([LINK], EDGES, NODES, courses, 0.0)[0]
        self.assertEqual(report["status"], "evaluated")
        self.assertEqual(report["precision"], "exact")
        self.assertEqual(report["residual_EN"], [0.0, 0.0])
        self.assertFalse(report["beyond_tolerance"])

    def test_wrong_course_is_reported_without_adjusting_it(self):
        courses = [
            {"seg": 0, "azimuth": 90.0, "distance": 100.0, "flags": []},
            {"seg": 1, "azimuth": 10.0, "distance": 100.0, "flags": []},
        ]
        report = cogo_assemble.validate_links([LINK], EDGES, NODES, courses, 0.0)[0]
        self.assertEqual(report["status"], "evaluated")
        self.assertGreater(report["misclosure"], 10.0)
        self.assertTrue(report["beyond_tolerance"])
        self.assertEqual(courses[1]["azimuth"], 10.0)

    def test_incomplete_course_is_not_evaluable(self):
        courses = [
            {"seg": 0, "azimuth": 90.0, "distance": 100.0, "flags": []},
            {"seg": 1, "azimuth": 0.0, "flags": ["partial_course"]},
        ]
        report = cogo_assemble.validate_links([LINK], EDGES, NODES, courses, 0.0)[0]
        self.assertEqual(report["status"], "not_evaluable")
        self.assertIn("edge_1_needs_one_complete_course", report["reason"])

    def test_malformed_link_is_reported_not_raised(self):
        report = cogo_assemble.validate_links(["not-a-link"], EDGES, NODES, [], 0.0)[0]
        self.assertEqual(report["status"], "not_evaluable")
        self.assertEqual(report["reason"], "each link must be an object")


if __name__ == "__main__":
    unittest.main()
