#!/usr/bin/env python3
"""Behavioral tests for maic-whiteboard-lint.

Uses the CLI end-to-end (subprocess) against fixtures constructed on the fly,
plus direct unit tests of the geometry module. Exercises exit codes, --json
parseability, out-of-canvas, line-crosses, the --only-last flag, and every
whiteboard walking.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin", "maic-whiteboard-lint")

sys.path.insert(0, BIN)
import importlib.util

spec = importlib.util.spec_from_loader("wl", loader=None)
import types

_loader = __import__("importlib.machinery", fromlist=["SourceFileLoader"])
wl = _loader.SourceFileLoader("wl", BIN).load_module()


class _Case(unittest.TestCase):
    def run_cli(self, args, cwd=None):
        return subprocess.run(
            [sys.executable, BIN] + args,
            cwd=cwd or ROOT, capture_output=True, text=True)


class TestGeometry(_Case):
    def test_overlap_threshold_math(self):
        a = {"x": 0, "y": 0, "w": 10, "h": 10}
        b = {"x": 5, "y": 0, "w": 10, "h": 10}  # inter 50, min 100 -> 0.5
        self.assertAlmostEqual(wl.relative_overlap(a, b), 0.5)
        # disjoint
        c = {"x": 100, "y": 100, "w": 10, "h": 10}
        self.assertEqual(wl.relative_overlap(a, c), 0.0)

    def test_overlap_threshold_boundary(self):
        # exactly 30%: intersection 30, min 100
        a = {"x": 0, "y": 0, "w": 10, "h": 10}
        b = {"x": 7, "y": 0, "w": 10, "h": 10}  # inter x=[7,10]=3, =30
        self.assertAlmostEqual(wl.relative_overlap(a, b), 0.3)

    def test_to_bbox_requires_numeric(self):
        self.assertIsNone(wl.to_bbox({"type": "text", "id": "a", "left": "x"}))
        el = {"type": "text", "id": "a", "left": 1, "top": 2, "width": 3, "height": 4}
        self.assertEqual(wl.to_bbox(el), {
            "id": "a", "type": "text", "label": wl.element_label(el),
            "x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0})

    def test_line_ignored_as_bbox(self):
        self.assertIsNone(wl.to_bbox({"type": "line", "left": 0, "top": 0}))

    def test_point_in_rect(self):
        b = {"x": 0, "y": 0, "w": 10, "h": 10}
        self.assertTrue(wl.point_in_rect(5, 5, b))
        self.assertFalse(wl.point_in_rect(20, 20, b))


class TestConflictDetection(_Case):
    def test_overlap_detected(self):
        els = [
            {"type": "text", "id": "a", "left": 100, "top": 100,
             "width": 200, "height": 80, "content": "<p>x</p>"},
            {"type": "text", "id": "b", "left": 150, "top": 120,
             "width": 200, "height": 80, "content": "<p>y</p>"},
        ]
        found = wl.detect_conflicts(els)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["type"], "OVERLAP")
        self.assertEqual(found[0]["percent"], 56)

    def test_non_overlapping_passes(self):
        els = [
            {"type": "text", "id": "a", "left": 0, "top": 0,
             "width": 100, "height": 50, "content": "<p>a</p>"},
            {"type": "text", "id": "b", "left": 200, "top": 0,
             "width": 100, "height": 50, "content": "<p>b</p>"},
        ]
        self.assertEqual(wl.detect_conflicts(els), [])

    def test_out_of_canvas(self):
        els = [{"type": "text", "id": "c", "left": 990, "top": -10,
                "width": 60, "height": 40, "content": "<p>c</p>"}]
        found = wl.detect_conflicts(els)
        types = [f["type"] for f in found]
        self.assertEqual(types, ["OUT_OF_CANVAS"])
        self.assertIn("right edge by 50px", found[0]["edge"])
        self.assertIn("top edge by 10px", found[0]["edge"])

    def test_line_crosses_bbox(self):
        els = [
            {"type": "text", "id": "box", "left": 100, "top": 100,
             "width": 100, "height": 100, "content": "<p>box</p>"},
            {"type": "line", "id": "ln", "left": 0, "top": 0,
             "start": [0, 150], "end": [300, 150]},  # horizontal through box
        ]
        found = wl.detect_conflicts(els)
        self.assertTrue(any(f["type"] == "LINE_CROSSES" for f in found))

    def test_line_endpoint_in_bbox(self):
        els = [
            {"type": "text", "id": "box", "left": 100, "top": 100,
             "width": 100, "height": 100, "content": "<p>box</p>"},
            {"type": "line", "id": "ln", "left": 0, "top": 0,
             "start": [150, 150], "end": [220, 220]},  # start inside bbox
        ]
        found = wl.detect_conflicts(els)
        self.assertTrue(any(f["type"] == "LINE_CROSSES" for f in found))

    def test_multiple_conflicts_counted(self):
        els = [
            {"type": "text", "id": "a", "left": 0, "top": 0,
             "width": 100, "height": 100, "content": "<p>a</p>"},
            {"type": "text", "id": "b", "left": 30, "top": 30,
             "width": 100, "height": 100, "content": "<p>b</p>"},  # 49% overlap
            {"type": "text", "id": "c", "left": -30, "top": -30,
             "width": 50, "height": 50, "content": "<p>c</p>"},  # out of canvas
        ]
        found = wl.detect_conflicts(els)
        types = sorted(f["type"] for f in found)
        self.assertEqual(types, ["OUT_OF_CANVAS", "OVERLAP"])

    def test_empty_elements(self):
        self.assertEqual(wl.detect_conflicts([]), [])

    def test_rotation_bbox_inflates(self):
        # a 100x100 square rotated 45deg has an AABB ~141x141
        el = {"type": "shape", "id": "a", "left": 0, "top": 0,
              "width": 100, "height": 100, "rotate": 45}
        rb = wl.rotated_bbox(el)
        self.assertIsNotNone(rb)
        self.assertAlmostEqual(rb["w"], 100 * 2 ** 0.5, places=2)

    def test_rotation_ignored_when_zero(self):
        el = {"type": "shape", "id": "a", "left": 0, "top": 0,
              "width": 100, "height": 100, "rotate": 0}
        self.assertIsNone(wl.rotated_bbox(el))

    def test_rotation_false_negative_detected(self):
        # plain overlap 25% passes upstream; rotated AABB is 32% -> collides
        els = [
            {"type": "shape", "id": "a", "left": 0, "top": 0,
             "width": 300, "height": 300, "rotate": 0},
            {"type": "shape", "id": "b", "left": 275, "top": 100,
             "width": 100, "height": 100, "rotate": 45},
        ]
        self.assertEqual(wl.detect_conflicts(els), [])  # upstream is silent
        blind = wl.detect_rotation_blindspots(els)["false_negatives"]
        self.assertEqual(len(blind), 1)
        self.assertEqual(blind[0]["plain_percent"], 25)
        self.assertGreaterEqual(blind[0]["rotated_percent"], 30)


class TestCLI(_Case):
    def write_fixture(self, d):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_exit_0_clean(self):
        p = self.write_fixture({"stage": {"whiteboard": [{"id": "w", "elements": [
            {"type": "text", "id": "a", "left": 0, "top": 0,
             "width": 100, "height": 50, "content": "<p>a</p>"},
        ]}]}})
        r = self.run_cli([p])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_exit_1_conflict(self):
        p = self.write_fixture({"stage": {"whiteboard": [{"id": "w", "elements": [
            {"type": "text", "id": "a", "left": 100, "top": 100,
             "width": 200, "height": 80, "content": "<p>a</p>"},
            {"type": "text", "id": "b", "left": 150, "top": 120,
             "width": 200, "height": 80, "content": "<p>b</p>"},
        ]}]}})
        r = self.run_cli([p])
        self.assertEqual(r.returncode, 1)

    def test_exit_2_missing_file(self):
        r = self.run_cli(["/nonexistent/x.json"])
        self.assertEqual(r.returncode, 2)

    def test_exit_2_bad_json(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write("{not json")
        self.addCleanup(os.remove, path)
        r = self.run_cli([path])
        self.assertEqual(r.returncode, 2)

    def test_json_parseable(self):
        p = self.write_fixture({"stage": {"whiteboard": [{"id": "w", "elements": [
            {"type": "text", "id": "a", "left": 0, "top": 0,
             "width": 100, "height": 50, "content": "<p>a</p>"},
        ]}]}})
        r = self.run_cli([p, "--json"])
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["whiteboards_linted"], 1)

    def test_json_conflict_report(self):
        p = self.write_fixture({"stage": {"whiteboard": [{"id": "w", "elements": [
            {"type": "text", "id": "a", "left": 100, "top": 100,
             "width": 200, "height": 80, "content": "<p>a</p>"},
            {"type": "text", "id": "b", "left": 150, "top": 120,
             "width": 200, "height": 80, "content": "<p>b</p>"},
        ]}]}})
        r = self.run_cli([p, "--json"])
        out = json.loads(r.stdout)
        self.assertFalse(out["ok"])
        self.assertEqual(out["conflicts_total"], 1)
        self.assertEqual(out["whiteboards"][0]["conflicts"][0]["type"], "OVERLAP")

    def test_lints_every_whiteboard_not_just_last(self):
        p = self.write_fixture({"stage": {"whiteboard": [
            {"id": "w1", "elements": [
                {"type": "text", "id": "a", "left": 100, "top": 100,
                 "width": 200, "height": 80, "content": "<p>a</p>"},
                {"type": "text", "id": "b", "left": 150, "top": 120,
                 "width": 200, "height": 80, "content": "<p>b</p>"},
            ]},
            {"id": "w2", "elements": [
                {"type": "text", "id": "c", "left": 0, "top": 0,
                 "width": 50, "height": 50, "content": "<p>c</p>"},
            ]},
        ]}})
        # default: lints both
        r = self.run_cli([p, "--json"])
        out = json.loads(r.stdout)
        self.assertEqual(out["whiteboards_linted"], 2)
        self.assertEqual(out["conflicts_total"], 1)
        # --only-last: lints only last (clean w2) -> exit 0
        r2 = self.run_cli([p, "--json", "--only-last"])
        out2 = json.loads(r2.stdout)
        self.assertEqual(out2["whiteboards_linted"], 1)
        self.assertTrue(out2["ok"])
        self.assertEqual(r2.returncode, 0)

    def test_scene_whiteboard_walked(self):
        p = self.write_fixture({"scenes": [
            {"id": "sc1", "whiteboard": [
                {"id": "wb", "elements": [
                    {"type": "text", "id": "a", "left": 100, "top": 100,
                     "width": 200, "height": 80, "content": "<p>a</p>"},
                    {"type": "text", "id": "b", "left": 150, "top": 120,
                     "width": 200, "height": 80, "content": "<p>b</p>"},
                ]}]},
        ]})
        r = self.run_cli([p, "--json"])
        self.assertEqual(r.returncode, 1)
        out = json.loads(r.stdout)
        self.assertEqual(out["whiteboards_linted"], 1)

    def test_no_whiteboard_input_error(self):
        p = self.write_fixture({"foo": "bar"})
        r = self.run_cli([p])
        self.assertEqual(r.returncode, 2)

    def test_upstream_text_parity(self):
        els = [
            {"type": "text", "id": "a", "left": 100, "top": 100,
             "width": 200, "height": 80, "content": "<p>x</p>"},
            {"type": "text", "id": "b", "left": 150, "top": 120,
             "width": 200, "height": 80, "content": "<p>y</p>"},
        ]
        txt = wl.upstream_text(wl.detect_conflicts(els))
        self.assertIn("OVERLAP", txt)
        self.assertIn("Layout Conflicts Detected", txt)

    def test_strict_rotation_fails_clean_plain(self):
        # upstream-clean board that has a rotation blind spot
        p = self.write_fixture({"stage": {"whiteboard": [{"id": "w", "elements": [
            {"type": "shape", "id": "a", "left": 0, "top": 0,
             "width": 300, "height": 300, "rotate": 0},
            {"type": "shape", "id": "b", "left": 275, "top": 100,
             "width": 100, "height": 100, "rotate": 45},
        ]}]}})
        # default: no real conflicts -> exit 0, blind spots reported
        r = self.run_cli([p, "--json"])
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["rotation_false_negatives_total"], 1)
        # strict: blind spot escalates to exit 1
        r2 = self.run_cli([p, "--json", "--strict-rotation"])
        self.assertEqual(r2.returncode, 1)
        self.assertFalse(json.loads(r2.stdout)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)