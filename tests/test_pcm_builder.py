import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('pcm_builder', ROOT / 'tools/pcm-samples/build.py')
flash = load('tunu_flash', ROOT / 'tunu-flash.py')


class PcmBuilderTests(unittest.TestCase):
    def test_default_and_opt_in_preserve_native_imports(self):
        original = (ROOT / 'app.lisp').read_text()
        source = builder.injected_app(original)
        self.assertNotIn('warp-samples-enabled', original)
        self.assertNotIn('(play-warp-samples)', original)
        self.assertIn('(def warp-samples-enabled true)', source)
        self.assertIn('(= cmd warp-song-number)', source)
        self.assertEqual(len(flash.source_imports(source.encode())), 15)
        self.assertEqual(flash.source_imports(original.encode()), flash.source_imports(source.encode())[:3])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overrides = {}
            for index, (_, tag) in enumerate(flash.source_imports(source.encode())):
                path = root / (tag + '.bin')
                length = 160 if tag == 'warp-sample-11' else 4000 if tag.startswith('warp-sample-') else 20
                path.write_bytes(bytes([(index + 1) % 255]) * length)
                overrides[tag] = path
            image, _ = flash.build_image(source.encode(), overrides=overrides)
            unpacked, imports = flash.unpack_blob(flash.validate_image(image))
            self.assertEqual(unpacked, source.encode())
            for tag, path in overrides.items():
                self.assertEqual(imports[tag], path.read_bytes() + b'\0')
                self.assertEqual(hashlib.sha256(imports[tag][:-1]).digest(), hashlib.sha256(path.read_bytes()).digest())

    def test_missing_or_duplicate_hook_is_rejected(self):
        source = (ROOT / 'app.lisp').read_text()
        with self.assertRaisesRegex(ValueError, 'insertion point'):
            builder.injected_app(source.replace('; @pcm-sample-dispatch', ''))
        with self.assertRaisesRegex(ValueError, 'insertion point'):
            builder.injected_app(source + '\n; @pcm-sample-dispatch\n')


if __name__ == '__main__':
    unittest.main()
