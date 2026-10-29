import shutil, subprocess, unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'node não instalado')
class TestAppsScript(unittest.TestCase):
    def test_planilha_simulada(self):
        r = subprocess.run(['node', str(Path(__file__).with_name('apps_script_mock.js'))], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('apps script OK', r.stdout)
