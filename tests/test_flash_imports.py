import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

SPEC = importlib.util.spec_from_file_location("tunu_flash", Path(__file__).resolve().parents[1] / "tunu-flash.py")
flash = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flash)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "native.bin").write_bytes(b"\x01\x02\x00\xff")

    def test_plain_source_compatible(self):
        source = b"(print 1)"
        image, blob = flash.build_image(source)
        self.assertEqual(blob, source + b"\x00\x00\x00")
        self.assertEqual(image[:4], struct.pack(">I", len(blob)))
        self.assertEqual(image[4:6], struct.pack(">H", flash.crc16(b"\0\0" + blob)))
        self.assertEqual(flash.validate_image(image), blob)

    def test_aligned_table_golden(self):
        source = b'(import "native.bin" \'lib)'
        image, blob = flash.build_image(source, self.root)
        start = len(source) + 3
        offset = (start + len(b"lib\0") + 8 + 3) & ~3
        expected = source + b"\0\0\1lib\0" + struct.pack(">II", offset, 5)
        expected += b"\0" * (offset - len(expected)) + b"\x01\x02\x00\xff\0"
        self.assertEqual(blob, expected)
        self.assertEqual(flash.unpack_blob(blob), (source, {"lib": b"\x01\x02\x00\xff\0"}))
        self.assertEqual(flash.validate_image(image), blob)

    def test_multiple_import_alignment(self):
        (self.root / "text").write_bytes(b"hello")
        source = b'(import "native.bin" \'a)\n(import "text" \'longer-name)'
        _, blob = flash.build_image(source, self.root)
        self.assertEqual(flash.unpack_blob(blob)[1], {"a": b"\1\2\0\xff\0", "longer-name": b"hello\0"})

    def test_comments_strings_multiline(self):
        source = b'; (import "missing" \'ignored)\n(print "(import hidden)")\n(import\n "native.bin" ; comment\n \'lib)'
        self.assertEqual(flash.source_imports(source), [("native.bin", "lib")])
        flash.build_image(source, self.root)

    def test_missing_malformed_duplicate_and_unused_fail(self):
        for source in [b'(import "missing" \'a)', b'(import path \'a)',
                       b'(import "native.bin" a)', b'(import "native.bin" \'a) (import "native.bin" \'a)',
                       b'(print 1)\0']:
            with self.subTest(source=source), self.assertRaises((ValueError, OSError)):
                flash.build_image(source, self.root)
        with self.assertRaises(ValueError):
            flash.build_image(b"(print 1)", overrides={"unused": self.root / "native.bin"})

    def test_explicit_resource_override(self):
        source = b'(import "pkg::midi@://vesc_packages/lib_midi/midi.vescpkg" \'midi)'
        with self.assertRaises(ValueError):
            flash.build_image(source)
        _, blob = flash.build_image(source, overrides={"midi": self.root / "native.bin"})
        self.assertEqual(flash.unpack_blob(blob)[1]["midi"], b"\1\2\0\xff\0")

    def package(self):
        _, blob = flash.build_image(b'(import "native.bin" \'lib)', self.root)
        lisp = b"\0\0" + blob
        raw = b"VESC Packet\0name\0" + struct.pack(">I", 4) + b"Test"
        raw += b"lispData\0" + struct.pack(">I", len(lisp)) + lisp
        package = struct.pack(">I", len(raw)) + zlib.compress(raw)
        (self.root / "test.vescpkg").write_bytes(package)
        return package

    def test_package_member_and_source(self):
        self.package()
        source = b'(import "pkg::lib@test.vescpkg" \'native) (import "pkg@test.vescpkg" \'code)'
        _, blob = flash.build_image(source, self.root)
        self.assertEqual(flash.unpack_blob(blob)[1], {
            "native": b"\1\2\0\xff\0",
            "code": b'(import "native.bin" \'lib)\0',
        })
        with self.assertRaises(ValueError):
            flash.build_image(b'(import "pkg::missing@test.vescpkg" \'bad)', self.root)

    def test_resource_root(self):
        self.package()
        source = b'(import "pkg::lib@://test.vescpkg" \'lib)'
        flash.build_image(source, resource_root=self.root)
        with self.assertRaises(ValueError):
            flash.build_image(b'(import "://../escape" \'bad)', resource_root=self.root)

    def test_malformed_packages(self):
        valid = self.package()
        for data in [b"", valid[:6], b"\0\0\0\1" + valid[4:], valid + b"junk",
                     struct.pack(">I", flash.MAX_PACKAGE_SIZE + 1) + valid[4:]]:
            with self.subTest(data=data[:8]), self.assertRaises((ValueError, zlib.error)):
                flash.package_lisp(data)

    def test_corrupt_images_and_flash_limit(self):
        image, blob = flash.build_image(b"(print 1)")
        for data in [b"", image[:-1], image[:4] + b"\xff\xff" + image[6:]]:
            with self.assertRaises(ValueError):
                flash.validate_image(data)
        with self.assertRaises(ValueError):
            flash.build_image(b"x" * flash.MAX_IMAGE_SIZE)
        image, _ = flash.image_from_blob(b'(import "x" \'x)\0\0\0')
        with self.assertRaises(ValueError):
            flash.validate_image(image)
        self.assertEqual(flash.image_from_blob(blob)[0], flash.build_image(b"(print 1)")[0])

    def test_malformed_tables(self):
        prefix = b"x\0\0\1a\0"
        for offset, size in [(0, 1), (17, 1), (1000, 1), (16, 100), (16, 0)]:
            with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                flash.unpack_blob(prefix + struct.pack(">II", offset, size) + b"\0" * 8)

    def test_invalid_input_never_touches_hardware(self):
        path = self.root / "app.lisp"
        path.write_text('(import "missing" \'lib)')
        with patch.object(sys, "argv", ["tunu-flash.py", "write", str(path)]), \
             patch.object(flash, "engine_set") as engine, patch.object(flash, "VescCan") as can:
            with self.assertRaises(SystemExit):
                flash.main()
            engine.assert_not_called()
            can.assert_not_called()

    def test_extract_source_and_blob_without_hardware(self):
        source = self.root / "app.lisp"
        source.write_text('(import "native.bin" \'lib)')
        _, blob = flash.build_image(source.read_bytes(), self.root)
        blob_path = self.root / "before.blob"
        blob_path.write_bytes(blob)
        for arguments in ([str(blob_path)], [str(source), "--source"]):
            with self.subTest(arguments=arguments):
                output = self.root / ("extracted-blob" if arguments[0] == str(blob_path) else "extracted-source")
                with patch.object(sys, "argv", ["tunu-flash.py", "extract", *arguments, "-o", str(output)]), \
                     patch.object(flash, "engine_set") as engine, patch.object(flash, "VescCan") as can:
                    self.assertEqual(flash.main(), 0)
                    engine.assert_not_called()
                    can.assert_not_called()
                self.assertEqual((output / "before.lisp").read_bytes(), source.read_bytes())
                self.assertEqual((output / "installed-lib.bin").read_bytes(), (self.root / "native.bin").read_bytes())
                metadata = flash.json.loads((output / "installed-imports.json").read_text())
                self.assertEqual(metadata[0]["length"], 5)
                self.assertEqual(metadata[0]["sha256"], flash.hashlib.sha256((self.root / "native.bin").read_bytes()).hexdigest())
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                self.assertEqual((output / "installed-lib.bin").stat().st_mode & 0o777, 0o600)

    def test_extract_package_member_and_invalid_or_existing_destination(self):
        self.package()
        source = self.root / "app.lisp"
        source.write_text('(import "pkg::lib@://test.vescpkg" \'midi)')
        output = self.root / "resources"
        with patch.object(sys, "argv", ["tunu-flash.py", "extract", str(source), "--source", "--resource-root", str(self.root), "-o", str(output)]):
            self.assertEqual(flash.main(), 0)
        self.assertEqual((output / "installed-midi.bin").read_bytes(), (self.root / "native.bin").read_bytes())
        with self.assertRaises(FileExistsError):
            flash.extract_imports(flash.build_image(b"(print 1)")[1], output)
        bad = self.root / "bad.blob"
        bad.write_bytes(b"not a blob")
        with patch.object(sys, "argv", ["tunu-flash.py", "extract", str(bad), "-o", str(self.root / "bad-output")]), \
             patch.object(flash, "engine_set") as engine, patch.object(flash, "VescCan") as can:
            with self.assertRaises(SystemExit):
                flash.main()
            engine.assert_not_called()
            can.assert_not_called()
        self.assertFalse((self.root / "bad-output").exists())

    def test_backup_writes_readback_and_valid_rollback(self):
        source = b'(import "native.bin" \'lib)'
        _, blob = flash.build_image(source, self.root)
        output = self.root / "backup"
        self.assertEqual(flash.extract_imports(blob, output, include_backup=True), 1)
        self.assertEqual((output / "before.blob").read_bytes(), blob)
        self.assertEqual(flash.validate_image((output / "rollback.img").read_bytes()), blob)
        self.assertEqual((output / "before.lisp").read_bytes(), source)
        self.assertEqual((output / "installed-lib.bin").read_bytes(), (self.root / "native.bin").read_bytes())

    def test_read_backup_cli_captures_full_image(self):
        source = b'(import "native.bin" \'lib)'
        _, blob = flash.build_image(source, self.root)
        output = self.root / "controller-backup"
        with patch.object(sys, "argv", ["tunu-flash.py", "--no-engine", "read", "--backup-dir", str(output)]), \
             patch.object(flash, "VescCan") as can, patch.object(flash, "cmd_read", return_value=blob), \
             patch.object(flash, "engine_set") as engine:
            self.assertEqual(flash.main(), 0)
            engine.assert_not_called()
            can.return_value.close.assert_called_once()
        self.assertEqual((output / "before.lisp").read_bytes(), source)
        self.assertEqual((output / "before.blob").read_bytes(), blob)
        self.assertEqual(flash.validate_image((output / "rollback.img").read_bytes()), blob)

    def test_read_existing_backup_dir_refuses_before_power(self):
        with patch.object(sys, "argv", ["tunu-flash.py", "read", "--backup-dir", str(self.root)]), \
             patch.object(flash, "engine_set") as engine, patch.object(flash, "VescCan") as can:
            with self.assertRaises(SystemExit):
                flash.main()
            engine.assert_not_called()
            can.assert_not_called()

    def test_offline_build_and_backup_restore(self):
        path = self.root / "app.lisp"
        output = self.root / "app.image"
        path.write_text('(import "native.bin" \'lib)')
        with patch.object(sys, "argv", ["tunu-flash.py", "build", str(path), "-o", str(output)]), \
             patch.object(flash, "engine_set") as engine, patch.object(flash, "VescCan") as can:
            self.assertEqual(flash.main(), 0)
            engine.assert_not_called()
            can.assert_not_called()
        original = output.read_bytes()
        backup = self.root / "backup.blob"
        backup.write_bytes(original[8:])
        with patch.object(sys, "argv", ["tunu-flash.py", "build", "--blob", str(backup), "-o", str(output)]):
            self.assertEqual(flash.main(), 0)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
