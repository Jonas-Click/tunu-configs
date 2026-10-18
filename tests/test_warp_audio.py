#!/usr/bin/env python3
"""Test deterministic signed-8 audio conversion without bundled media."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "warp_audio_converter", ROOT / "tools/warp-sound/convert_audio.py")
converter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(converter)


class AudioConverterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "input.audio"
        self.source.write_bytes(b"fixture")
        self.output = self.root / "private/output.s8"
        self.sample = bytes((index % 256 for index in range(converter.SAMPLE_BYTES)))

    def fake_ffmpeg(self, command, **kwargs):
        Path(command[-1]).write_bytes(self.sample)
        self.command = command

    @mock.patch.object(converter.shutil, "which", return_value="/fake/ffmpeg")
    def test_converts_to_fixed_mono_signed_8_bit_format_and_checks_hash(self, _which):
        digest = hashlib.sha256(self.sample).hexdigest()
        with mock.patch.object(converter.subprocess, "run", side_effect=self.fake_ffmpeg):
            result = converter.convert_audio(self.source, self.output, digest)
        self.assertEqual(result, digest)
        self.assertEqual(self.output.read_bytes(), self.sample)
        self.assertIn("-map", self.command)
        self.assertIn("0:a:0", self.command)
        self.assertEqual(self.command[self.command.index("-f") + 1], "s8")
        self.assertEqual(self.command[self.command.index("-ac") + 1], "1")
        self.assertEqual(self.command[self.command.index("-ar") + 1], "16000")

    @mock.patch.object(converter.shutil, "which", return_value="/fake/ffmpeg")
    def test_new_output_directory_is_compatible_with_private_builder(self, _which):
        previous = os.umask(0)
        try:
            with mock.patch.object(converter.subprocess, "run", side_effect=self.fake_ffmpeg):
                converter.convert_audio(self.source, self.output)
        finally:
            os.umask(previous)
        self.assertEqual(self.output.parent.stat().st_mode & 0o777, 0o700)

    @mock.patch.object(converter.shutil, "which", return_value="/fake/ffmpeg")
    def test_failed_validation_preserves_existing_output_and_removes_temporary_file(self, _which):
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"prior")
        with mock.patch.object(converter.subprocess, "run", side_effect=self.fake_ffmpeg):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                converter.convert_audio(self.source, self.output, "0" * 64)
        self.assertEqual(self.output.read_bytes(), b"prior")
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_rejects_using_same_file_for_source_and_output(self):
        with self.assertRaisesRegex(ValueError, "different files"):
            converter.convert_audio(self.source, self.source)

    def test_rejects_source_or_output_inside_repository(self):
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            converter.convert_audio(ROOT / "recording.mp3", self.output)
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            converter.convert_audio(self.source, ROOT / "sample.s8")


if __name__ == "__main__":
    unittest.main()
