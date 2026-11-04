import os
import sys
import unittest
from unittest import mock

import requests

import test_skus          # define as variáveis EXP_* de teste
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app as app_mod
import conhecimento
import fontes

URL = 'https://script.google.com/macros/s/x/exec'
RESP = {'ok': True, 'geral': {'prompt': 'P', 'regras': ['R1', '', 'R2'], 'respostas': ['FRETE\nx']},
        'skus': {'VP4O_5m2': {'encontrado': True, 'familia': 'VP50', 'regras_especiais': 'Este SKU possui 5 m².', 'base_encontrada': True,
                              'base': [{'topico': 'O QUE É', 'texto': 'Manta'}, {'topico': 'VAZIO', 'texto': ''}]}, 'X': {'encontrado': False}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.corpos, self.resp, self.cai = [], RESP, False
        for x in (mock.patch.object(fontes._SESS, 'post', self.post), mock.patch.dict(os.environ, {'EXP_CONHECIMENTO_URL': URL, 'EXP_CONHECIMENTO_KEY': 'k'})):
            x.start(); self.addCleanup(x.stop)
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)

    def post(self, url, json=None, **kw):
        self.corpos.append((url, json))
        if self.cai:
            raise requests.ConnectionError('caiu')
        return mock.Mock(raise_for_status=lambda: None, json=lambda: self.resp)

    def ler(self, skus):
        return self.c.post('/api/conhecimento/ler', json={'skus': skus})


class TestLer(Base):
    def test_le_e_limpa(self):
        d = self.ler(['VP4O_5m2', 'X', 'VP4O_5m2']).get_json()
        self.assertTrue(d['ok'] and d['somente_leitura'])
        self.assertEqual(d['skus']['VP4O_5m2']['familia'], 'VP50')
        self.assertEqual(d['skus']['VP4O_5m2']['base'], [{'topico': 'O QUE É', 'texto': 'Manta'}])      # tópico vazio fica de fora
        self.assertFalse(d['skus']['X']['encontrado'])
        url, corpo = self.corpos[0]
        self.assertEqual((url, corpo['acao'], corpo['chave'], corpo['skus']), (URL, 'conhecimento_sku', 'k', ['VP4O_5m2', 'X']))   # sem repetir

    def test_nunca_escreve_so_le(self):
        self.ler(['X'])
        self.assertEqual({c[1]['acao'] for c in self.corpos}, {'conhecimento_sku'})

    def test_falhas_explicam_o_que_fazer(self):
        self.cai = True
        self.assertEqual(self.ler(['X']).status_code, 503)
        self.cai, self.resp = False, {'ok': False, 'error': 'Chave inválida.'}
        r = self.ler(['X'])
        self.assertEqual(r.status_code, 503)
        self.assertIn('Chave inválida.', r.get_json()['error'])
        self.assertIn('conhecimento_sku.gs', r.get_json()['error'])
        with mock.patch.dict(os.environ, {'EXP_CONHECIMENTO_KEY': ''}):
            self.assertIn('EXP_CONHECIMENTO_URL', self.ler(['X']).get_json()['error'])

    def test_entradas_invalidas(self):
        self.assertEqual(self.ler('A').status_code, 400)
        self.assertEqual(self.ler(['s%d' % i for i in range(11)]).status_code, 400)

    def test_login_obrigatorio(self):
        self.assertEqual(app_mod.app.test_client().post('/api/conhecimento/ler', json={}).status_code, 401)


class TestPrompt(unittest.TestCase):
    def test_texto_vazio_sem_nada_util(self):
        self.assertEqual(conhecimento.para_prompt({'geral': {'prompt': '', 'regras': [], 'respostas': []}, 'skus': {'A': {'regras_especiais': '', 'base': [], 'familia': ''}}}), '')


if __name__ == '__main__':
    unittest.main()
