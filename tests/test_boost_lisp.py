import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('drive_forms', ROOT / 'tests/test_drive_limits.py')
forms_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forms_module)


class BoostLispTests(unittest.TestCase):
    def test_generic_build_stays_disabled_and_both_native_configs_stay_normal(self):
        source = (ROOT / 'app.lisp').read_text()
        self.assertIn('(def boost-native-enabled false)', source)
        self.assertIn('(def boost-policy-enabled false)', source)
        self.assertIn('(def boost-per-pack-amps 85.0)', source)
        self.assertIn('; @boost-native-imports', source)
        self.assertNotIn('(import "boost_guard.bin"', source)
        for name in ('vesc_mcconf.xml', '3kw-vesc_mcconf.xml'):
            self.assertIn('<l_in_current_max>60</l_in_current_max>', (ROOT / name).read_text())
            self.assertIn('<l_current_max>210</l_current_max>', (ROOT / name).read_text())

    def test_actual_lisp_adapter_with_mocked_native_api(self):
        repl = os.environ.get('LBM_REPL', 'lbm')
        if not shutil.which(repl):
            self.skipTest('set LBM_REPL to the host LispBM executable')
        source = (ROOT / 'app.lisp').read_text()
        selected = []
        for form in forms_module.forms(source):
            if form.startswith('(def boost-') or form.startswith('(defun boost-') or form.startswith('(defun send-status4 '):
                selected.append(form)
        program = '''(def failures 0)
(def loaded 0)
(def configured 0)
(def fed 0)
(def reported 0)
(def sent 0)
(def heartbeat-ok true)
(def api-version 1)
(def driving false)
(def boost-guard-lib (array-create 8))
(def dataArray_0x7E3 (array-create 8))
(defun expect (value label) {
    (if (not value) {
        (def failures (+ failures 1))
        (print (list "FAIL" label))
    })
})
(defun drive-requested () driving)
(defun load-native-lib (value) { (def loaded (+ loaded 1)) true })
(defun ext-boost-api () api-version)
(defun ext-boost-config (amps duration cooldown heartbeat supply release) {
    (expect (and (= amps 85.0) (= duration 10.0) (= cooldown 90.0)
                 (= heartbeat 250) (= supply 3000) (= release 100)) "parameters")
    (def configured (+ configured 1)) true
})
(defun ext-boost-heartbeat (control drive) {
    (expect (and (= control boost-control-byte) (= drive (if driving 1 0))) "lease inputs")
    (def fed (+ fed 1)) heartbeat-ok
})
(defun ext-boost-normal-current () 60.0)
(defun ext-boost-normal (amps packs) (and (= amps 60.0) (= packs 1)))
(defun ext-boost-supply (packs amps) (and (= packs 1) (= amps 60.0)))
(defun ext-boost-report () { (def reported (+ reported 1)) true })
(defun can-send-sid (id data) { (expect (= id 0x7E3) "legacy status id") (def sent (+ sent 1)) })
(defun conf-get (parameter) 60.0)
'''
        program += '\n'.join(selected) + '''
(boost-start)
(expect (and (= loaded 0) (not boost-runtime-ready)) "generic boot disabled")
(send-status4 0 1)
(expect (and (= sent 1) (= (bufget-u8 dataArray_0x7E3 0) 0x99)) "legacy disabled status")
(def boost-native-enabled true)
(boost-start)
(expect (= loaded 0) "policy gate")
(def boost-policy-enabled true)
(def api-version 0)
(boost-start)
(expect (and (not boost-runtime-ready) (= configured 0)) "API gate")
(def api-version 1)
(boost-start)
(expect (and boost-runtime-ready (= configured 1)) "native startup")
(def driving true)
(def boost-control-byte 7)
(boost-tick)
(expect (= fed 1) "drive lease")
(expect (= (boost-normal-current) 60.0) "normal cap readback")
(expect (boost-set-normal-current 60.0 1) "normal cap update")
(expect (boost-supply-lease 1 60.0) "supply lease")
(send-status4 1 1)
(expect (and (= reported 1) (= sent 1)) "native status ownership")
(def heartbeat-ok false)
(boost-tick)
(expect (not boost-runtime-ready) "failed native lease disables Lisp adapter")
(send-status4 0 1)
(expect (= sent 2) "fallback status")
(if (= failures 0) (print "BOOST_LISP_PASS") (print "BOOST_LISP_FAIL"))
'''
        with tempfile.TemporaryDirectory(prefix='tunu-boost-lisp-') as directory:
            path = Path(directory) / 'test.lisp'
            path.write_text(program.replace('ext-boost-', 'mock-boost-'))
            result = subprocess.run([repl, '--silent', '--history_file', '', '--terminate',
                                     '--heap_size=131072', '--memory_size=4194304', '--src', str(path)],
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('BOOST_LISP_PASS', result.stdout)
        self.assertNotIn('BOOST_LISP_FAIL', result.stdout)


if __name__ == '__main__':
    unittest.main()
