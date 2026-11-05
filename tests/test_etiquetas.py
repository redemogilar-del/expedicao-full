import os
import sys
import unittest
from unittest import mock

os.environ['EXP_SECRET_KEY'] = 'teste'
os.environ['EXP_LOGIN_USER'] = 'admin'
from werkzeug.security import generate_password_hash
os.environ['EXP_LOGIN_PASSWORD_HASH'] = generate_password_hash('senha-de-teste')
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_mod
import fontes


class EtiquetasTest(unittest.TestCase):
    def setUp(self):
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)

    def test_exige_login(self):
        r = app_mod.app.test_client().post('/api/vendas/etiquetas-marcar', json={})
        self.assertIn(r.status_code, (302, 401))

    def test_marcar_envia_so_o_que_vale(self):
        visto = {}
        with mock.patch.object(fontes, 'etiquetas_registrar', lambda regs: (visto.update(regs=regs), {'gravados': 1, 'atualizados': 0})[1]):
            r = self.c.post('/api/vendas/etiquetas-marcar', json={'situacao': 'GERADA', 'registros': [{'conta': 'ML19', 'pedido': ' 123 ', 'sku': 'A_1', 'quantidade': 2, 'envio_id': '9'}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(visto['regs'], [{'conta': 'ML19', 'pedido': '123', 'envio_id': '9', 'sku': 'A_1', 'quantidade': 2.0, 'situacao': 'GERADA'}])

    def test_recusa_situacao_conta_e_tamanho_invalidos(self):
        for corpo in ({'situacao': 'X', 'registros': [{'conta': 'ML19', 'pedido': '1'}]},
                      {'situacao': 'GERADA', 'registros': [{'conta': 'ML99', 'pedido': '1'}]},
                      {'situacao': 'GERADA', 'registros': []},
                      {'situacao': 'GERADA', 'registros': [{'conta': 'ML19', 'pedido': '1'}] * 201}):
            with mock.patch.object(fontes, 'etiquetas_registrar', side_effect=AssertionError('não podia chamar')):
                self.assertEqual(self.c.post('/api/vendas/etiquetas-marcar', json=corpo).status_code, 400)

    def test_ler_devolve_situacoes(self):
        with mock.patch.object(fontes, 'etiquetas_ler', lambda conta, ped: {'1': {'situacao': 'GERADA', 'data': '05/11/2026 07:00'}}):
            r = self.c.post('/api/vendas/etiquetas-ler', json={'conta': 'ML44', 'pedidos': ['1', '2']})
        self.assertEqual(r.get_json()['etiquetas']['1']['situacao'], 'GERADA')

    def test_planilha_sem_trecho_novo_explica(self):
        resp = mock.Mock(); resp.raise_for_status = lambda: None; resp.json.return_value = {'ok': False}
        with mock.patch.dict(os.environ, {'EXP_SHEETS_URL': 'https://script.google.com/macros/s/x/exec', 'EXP_SHEETS_KEY': 'k'}), mock.patch.object(fontes._SESS, 'post', return_value=resp):
            r = self.c.post('/api/vendas/etiquetas-ler', json={'conta': 'ML19', 'pedidos': ['1']})
        self.assertEqual(r.status_code, 503)
        self.assertIn('etiquetas.gs', r.get_json()['error'])

    def test_calcular_mantem_sku_original(self):
        linha = {'conta': 'ML19', 'pedido': '1', 'mlb': 'MLB1', 'sku': 'NOVO', 'sku_orig': 'ANTIGO', 'quantidade': 1, 'venda': 100, 'tarifa': 10, 'frete': 5, 'grupo': 'a_enviar'}
        r = self.c.post('/api/vendas/calcular', json={'linhas': [linha], 'cadastro': {'NOVO': {'custo': 50, 'margem': 10, 'margem_full': 10}}, 'parametros': {'imposto_pct': '5', 'margem_padrao': '20'}})
        self.assertEqual(r.status_code, 200)
        l = r.get_json()['linhas'][0]
        self.assertEqual((l['sku'], l['sku_orig']), ('NOVO', 'ANTIGO'))
        self.assertEqual(l['custo_unit'], 50)


if __name__ == '__main__':
    unittest.main()
