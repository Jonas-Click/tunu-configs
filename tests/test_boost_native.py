import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeBoostTests(unittest.TestCase):
    def compile_and_run(self, source, marker, mock=False):
        compiler = os.environ.get("CC", "cc")
        if not shutil.which(compiler):
            self.skipTest("a host C compiler is required")
        with tempfile.TemporaryDirectory(prefix="tunu-boost-tests-") as directory:
            executable = str(Path(directory) / "test")
            command = [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                       "-fsanitize=address,undefined", "-I" + str(ROOT / "tools/boost")]
            if mock:
                command += ["-I" + str(ROOT / "tests/boost_mock")]
            command += [str(ROOT / "tests" / source), str(ROOT / "tools/boost/boost_core.c"),
                        "-lm", "-o", executable]
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            result = subprocess.run([executable], check=True, capture_output=True, text=True, timeout=30)
            self.assertIn(marker, result.stdout)

    def test_native_state_machine(self):
        self.compile_and_run("boost_core_test.c", "BOOST_CORE_PASS")

    def test_native_adapter_and_unload(self):
        self.compile_and_run("boost_guard_test.c", "BOOST_GUARD_PASS", mock=True)


if __name__ == "__main__":
    unittest.main()
