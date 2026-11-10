import shutil, subprocess, unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'node não instalado')
class TestAppsScript(unittest.TestCase):
    def test_planilha_simulada(self):
        r = subprocess.run(['node', str(Path(__file__).with_name('apps_script_mock.js'))], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('apps script OK', r.stdout)

    def test_conhecimento_sku(self):
        r = subprocess.run(['node', str(Path(__file__).with_name('conhecimento_mock.js'))], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('conhecimento apps script OK', r.stdout)

    def test_token_shopee(self):
        r = subprocess.run(['node', str(Path(__file__).with_name('shopee_token_mock.js'))], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('shopee token OK', r.stdout)
