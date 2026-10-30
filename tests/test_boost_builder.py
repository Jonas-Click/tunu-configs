import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('boost_flash', ROOT / 'tunu-flash.py')
flash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flash)


class BoostBuilderTests(unittest.TestCase):
    def setup_inputs(self, directory):
        root = Path(directory)
        backup = root / 'resources'
        backup.mkdir()
        metadata = []
        for name in ('midi', 'song1-track0', 'song1-track1'):
            data = ('resource:' + name).encode()
            (backup / ('installed-' + name + '.bin')).write_bytes(data)
            metadata.append({'tag': name, 'length': len(data) + 1,
                             'sha256': hashlib.sha256(data).hexdigest()})
        (backup / 'installed-imports.json').write_text(json.dumps(metadata))
        native = root / 'boost_guard.bin'
        native.write_bytes(bytes(32))
        manifest = {'api': 1, 'sdk_sha256': 'a458d0639460591c34d9eb14b5ba1c5a93659772e3320790449cf62fcae15937',
                    'bytes': 32, 'sha256': hashlib.sha256(native.read_bytes()).hexdigest()}
        native.with_suffix('.json').write_text(json.dumps(manifest))
        return root, backup, native

    def build(self, root, backup, native, *extra):
        return subprocess.run([sys.executable, str(ROOT / 'tools/boost/build.py'),
                               '--native-bin', str(native), '--resource-backup', str(backup),
                               '--output-dir', str(root / 'output'), *extra],
                              capture_output=True, text=True, timeout=20)

    def test_opt_in_image_preserves_resources_and_does_not_enable_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root, backup, native = self.setup_inputs(directory)
            result = self.build(root, backup, native)
            self.assertEqual(result.returncode, 0, result.stderr)
            image = (root / 'output/boost.img').read_bytes()
            source, imports = flash.unpack_blob(flash.validate_image(image))
            self.assertIn(b'(def boost-native-enabled true)', source)
            self.assertIn(b'(def boost-policy-enabled false)', source)
            self.assertEqual(imports['boost-guard-lib'], native.read_bytes() + b'\0')
            self.assertEqual(set(imports), {'boost-guard-lib', 'midi', 'song1-track0', 'song1-track1'})
            for name in ('midi', 'song1-track0', 'song1-track1'):
                self.assertEqual(imports[name], (backup / ('installed-' + name + '.bin')).read_bytes() + b'\0')
            self.assertEqual((root / 'output').stat().st_mode & 0o777, 0o700)

    def test_policy_requires_explicit_enable(self):
        with tempfile.TemporaryDirectory() as directory:
            root, backup, native = self.setup_inputs(directory)
            result = self.build(root, backup, native, '--enable-policy')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('(def boost-policy-enabled true)', (root / 'output/app.lisp').read_text())

    def test_binary_hash_mismatch_and_unreviewed_sdk_are_rejected(self):
        for wrong in ('binary', 'sdk'):
            with self.subTest(wrong=wrong), tempfile.TemporaryDirectory() as directory:
                root, backup, native = self.setup_inputs(directory)
                if wrong == 'binary':
                    native.write_bytes(b'X' * 32)
                else:
                    metadata = json.loads(native.with_suffix('.json').read_text())
                    metadata['sdk_sha256'] = 'unknown'
                    native.with_suffix('.json').write_text(json.dumps(metadata))
                result = self.build(root, backup, native)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / 'output/boost.img').exists())

    def test_generated_image_does_not_depend_on_private_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root, backup, native = self.setup_inputs(directory)
            result = self.build(root, backup, native)
            self.assertEqual(result.returncode, 0, result.stderr)
            source = (root / 'output/app.lisp').read_text()
            self.assertNotIn('(import "samples/', source)
            self.assertNotIn('(def warp-samples-enabled true)', source)


if __name__ == '__main__':
    unittest.main()
