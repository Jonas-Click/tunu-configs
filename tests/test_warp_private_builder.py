#!/usr/bin/env python3
"""Test private-build injection without private media or VESC resources."""
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "warp_private_builder", ROOT / "tools/warp-sound/build_private.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class PrivateBuilderTests(unittest.TestCase):
    def setUp(self):
        self.default_source = (ROOT / "app.lisp").read_text()

    def test_default_app_has_no_sample_import_or_song_seven_dispatch(self):
        self.assertNotIn("samples/warp-sample-", self.default_source)
        self.assertNotIn("warp-samples-enabled", self.default_source)
        self.assertNotIn("warp-song-number", self.default_source)
        self.assertNotIn("(= cmd warp-song-number)", self.default_source)

    def test_private_overlay_injects_all_chunks_and_generation_owned_controls(self):
        source = builder.injected_app(self.default_source)
        imports = re.findall(
            r'\(import "samples/warp-sample-(\d+)\.s8" \'warp-sample-\d+\)', source)
        self.assertEqual(sorted(map(int, imports)), list(range(12)))
        self.assertIn("(def warp-samples-enabled true)", source)
        self.assertIn("(def warp-song-number 7)", source)
        self.assertIn("(def music-play-generation 0)", source)
        self.assertIn("((and warp-samples-enabled (= cmd warp-song-number))", source)
        self.assertNotIn("@private-warp-", source)
        for name in builder.PRIVATE_FUNCTIONS:
            matches = re.findall(r"\(defun " + re.escape(name) + r"\b", source)
            self.assertEqual(len(matches), 1, name)
        self.assertIn("(var generation (advance-music-generation))",
                      builder.PRIVATE_FUNCTIONS["stop-music"])
        self.assertIn("(var generation (advance-music-generation))",
                      builder.PRIVATE_FUNCTIONS["play-current-song"])
        self.assertIn("(var generation (advance-music-generation))",
                      builder.PRIVATE_FUNCTIONS["play-axelf"])

    def test_reusable_tools_have_no_machine_specific_paths(self):
        for path in (ROOT / "tools/warp-sound/build_private.py",
                     ROOT / "tools/warp-sound/convert_audio.py",
                     ROOT / "tools/warp-sound/README.md"):
            text = path.read_text()
            self.assertNotIn("/home/", text)
            self.assertNotIn(".local/state", text)

    def test_private_input_and_output_paths_cannot_target_repository(self):
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            builder.require_private_path(ROOT / "private-media.s8", "sample")

    def test_function_replacement_skips_comments_and_parentheses_in_strings(self):
        source = '(defun target () { (print "(ignored)") ; ) ignored\n true })\n(defun other () nil)'
        changed = builder.replace_defun(source, "target", "(defun target () false)")
        self.assertEqual(changed,
                         '(defun target () false)\n(defun other () nil)')


if __name__ == "__main__":
    unittest.main()
