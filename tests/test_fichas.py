import os
import sys
import unittest
from unittest import mock

import requests

import test_skus          # define as variáveis EXP_* de teste
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app as app_mod
import fichas
import fontes

URL = 'https://script.google.com/macros/s/x/exec'


class Planilha:
    """Apps Script simulado: guarda fichas por SKU sem diferenciar maiúsculas."""
    def __init__(self):
        self.d, self.corpos, self.ignora, self.sem_script, self.cai = {}, [], False, False, False

    def post(self, url, json=None, **kw):
        self.corpos.append(json)
        if self.cai:
            raise requests.ConnectionError('caiu')
        if self.sem_script:
            return mock.Mock(raise_for_status=lambda: None, json=lambda: {'ok': False, 'error': 'Ação desconhecida'})
        if json['acao'] == 'fichas_sku_ler':
            achadas = {s.strip().lower(): {'texto': self.d[s.strip().lower()], 'atualizado': '04/11/2026 10:00'} for s in json['skus'] if self.d.get(s.strip().lower())}
            return mock.Mock(raise_for_status=lambda: None, json=lambda: {'ok': True, 'fichas': achadas})
        if not self.ignora:
            self.d[json['sku'].strip().lower()] = json['texto']
        return mock.Mock(raise_for_status=lambda: None, json=lambda: {'ok': True})


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Planilha()
        for x in (mock.patch.object(fontes._SESS, 'post', self.p.post), mock.patch.dict(os.environ, {'EXP_SHEETS_URL': URL, 'EXP_SHEETS_KEY': 'k'})):
            x.start(); self.addCleanup(x.stop)
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)

    def salvar(self, sku='GEO2', texto='Gramatura 150 g/m².', confirmacao=fichas.CONFIRMACAO):
        return self.c.post('/api/fichas/salvar', json={'sku': sku, 'texto': texto, 'confirmacao': confirmacao})


class TestLer(Base):
    def test_le_so_quem_tem_ficha_sem_ligar_para_maiusculas(self):
        self.p.d['geo2'] = 'Rolo 2 m x 50 m'
        d = self.c.post('/api/fichas/ler', json={'skus': ['GEO2', 'OUTRO', 'GEO2']}).get_json()
        self.assertEqual(list(d['fichas']), ['GEO2'])
        self.assertEqual(d['fichas']['GEO2']['texto'], 'Rolo 2 m x 50 m')
        self.assertEqual(self.p.corpos[0]['skus'], ['GEO2', 'OUTRO'])          # sem repetir

    def test_falha_da_planilha_nao_vira_sem_ficha(self):
        self.p.cai = True
        r = self.c.post('/api/fichas/ler', json={'skus': ['A']})
        self.assertEqual(r.status_code, 503)
        self.p.cai, self.p.sem_script = False, True
        r = self.c.post('/api/fichas/ler', json={'skus': ['A']})
        self.assertIn('fichas_sku.gs', r.get_json()['error'])

    def test_entradas_invalidas(self):
        self.assertEqual(self.c.post('/api/fichas/ler', json={'skus': 'A'}).status_code, 400)
        self.assertEqual(self.c.post('/api/fichas/ler', json={'skus': ['s%d' % i for i in range(21)]}).status_code, 400)

    def test_sem_planilha_configurada(self):
        with mock.patch.dict(os.environ, {'EXP_SHEETS_URL': ''}):
            self.assertEqual(self.c.post('/api/fichas/ler', json={'skus': ['A']}).status_code, 503)


class TestSalvar(Base):
    def test_salva_e_confere(self):
        d = self.salvar().get_json()
        self.assertEqual((d['status'], d['texto']), ('OK', 'Gramatura 150 g/m².'))
        self.assertEqual(self.p.d['geo2'], 'Gramatura 150 g/m².')

    def test_exige_confirmacao_e_valida(self):
        for r in (self.salvar(confirmacao=''), self.salvar(sku=' '), self.salvar(sku='a\nb'), self.salvar(sku='x' * 81), self.salvar(texto='x' * 2001)):
            self.assertEqual(r.status_code, 400)
        self.assertEqual(self.p.corpos, [])

    def test_texto_vazio_apaga(self):
        self.salvar()
        d = self.salvar(texto='').get_json()
        self.assertEqual((d['status'], d['mensagem']), ('OK', 'Ficha apagada.'))

    def test_planilha_que_aceita_mas_nao_grava_nao_vira_ok(self):
        self.p.ignora = True
        self.assertEqual(self.salvar().get_json()['status'], 'NAO_CONFIRMADO')

    def test_login_obrigatorio(self):
        anon = app_mod.app.test_client()
        self.assertEqual(anon.post('/api/fichas/salvar', json={}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
