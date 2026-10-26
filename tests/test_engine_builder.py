import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('engine_builder', ROOT / 'tools/engine-sound/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class EngineBuilderTests(unittest.TestCase):
    def legacy_source(self):
        source = (ROOT / 'app.lisp').read_text()
        names = re.findall(r'^\(defun ((?:engine-[A-Za-z0-9_-]+)|handle-engine-command|send-engine-status(?:-locked)?)\s', source, re.M)
        for name in names:
            source = builder.warp.replace_defun(source, name, '')
        source = re.sub(r'^\(def engine-[A-Za-z0-9_-]+ .+\)\n', '', source, flags=re.M)
        for line in ('    (engine-init)\n', '    (spawn 300 engine-loop)\n',
                     '    (def dataArray_0x7ED (array-create 4))\n',
                     '    (if (= id 0x4EAu32) (handle-engine-command data))\n',
                     '        (send-engine-status)\n'):
            source = source.replace(line, '')
        source = source.replace('(list abs sin loopforeach looprange loopwhile)',
                                '(list abs loopforeach looprange loopwhile)')
        source = source.replace('(def sw-app-version 3)', '(def sw-app-version 1)')
        source = builder.warp.replace_defun(source, 'send-status2', '(defun send-status2 () 25)')
        return source

    def inputs(self, root):
        backup = root / 'backup'
        backup.mkdir()
        source = self.legacy_source()
        (backup / 'before.lisp').write_text(source)
        metadata = []
        for _, tag in builder.flash.source_imports(source.encode()):
            raw = ('target-resource:' + tag).encode()
            (backup / ('installed-' + tag + '.bin')).write_bytes(raw)
            metadata.append({'tag': tag, 'length': len(raw) + 1, 'sha256': hashlib.sha256(raw).hexdigest()})
        (backup / 'installed-imports.json').write_text(json.dumps(metadata))
        return backup, source

    def test_target_logic_and_every_import_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup, original = self.inputs(root)
            output = root / 'output'
            metadata = builder.build(backup, output)
            source, imports = builder.flash.unpack_blob(builder.flash.validate_image((output / 'engine.img').read_bytes()))
            source = source.decode()
            self.assertIn('(def sw-app-version 3)', source)
            self.assertIn('(defun send-status2 () 25)', source)
            self.assertIn('(spawn 300 engine-loop)', source)
            self.assertIn('(if (= id 0x4EAu32) (handle-engine-command data))', source)
            self.assertIn('\n(def engine-enabled false)\n', source)
            self.assertIsNone(re.search(r'^\(def engine-enabled true\)$', source, re.M))
            for name in builder.PROTECTED_FUNCTIONS:
                self.assertEqual(builder.warp.extract_defun(original, name), builder.warp.extract_defun(source, name))
            for tag, data in imports.items():
                self.assertEqual(data, (backup / ('installed-' + tag + '.bin')).read_bytes() + b'\0')
            self.assertEqual(metadata['app_version'], 3)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)

    def test_existing_engine_or_missing_hook_requires_review(self):
        with self.assertRaisesRegex(ValueError, 'already contains engine sound'):
            builder.add_engine((ROOT / 'app.lisp').read_text())
        source = self.legacy_source().replace('(spawn 200 decay-loop)', '(spawn 250 decay-loop)')
        with self.assertRaisesRegex(ValueError, 'expected one source hook'):
            builder.add_engine(source)

    def test_corrupt_resource_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup, _ = self.inputs(root)
            (backup / 'installed-midi.bin').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'backup import mismatch'):
                builder.build(backup, root / 'output')

    def test_backport_does_not_add_unrelated_boost(self):
        source = self.legacy_source()
        source = re.sub(r'^\(def boost-[A-Za-z0-9_-]+ .+\)\n', '', source, flags=re.M)
        result = builder.add_engine(source)
        self.assertNotIn('(def boost-native-enabled', result)
        self.assertNotIn('(def boost-policy-enabled', result)


if __name__ == '__main__':
    unittest.main()
