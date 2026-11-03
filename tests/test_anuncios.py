import os
import sys
import unittest
from unittest import mock

import test_skus          # define as variáveis EXP_* de teste
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app as app_mod
import anuncios
import fontes
import skus
from test_skus import FakeML


class Fake(FakeML):
    def put(self, url, json=None, **kw):
        if json and 'status' in json:
            self.puts.append((url, json))
            if self.recusa:
                return mock.Mock(ok=False, status_code=400, text='recusado')
            if not self.ignora:
                self.itens[url.split('/')[-1]]['status'] = json['status']
            return mock.Mock(ok=True, status_code=200, text='')
        return super().put(url, json, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        anuncios._RECENTES.clear(); skus._RECENTES.clear()
        self.fake = Fake()
        self.p = self.fake.patches()
        for x in self.p:
            x.start()
        self.addCleanup(lambda: [x.stop() for x in self.p])
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)

    def post(self, rota, corpo):
        return self.c.post(rota, json=corpo)


class TestPausar(Base):
    def test_pausa_so_ativo_e_confere(self):
        d = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': [{'mlb': 'MLB100000001'}, {'mlb': 'MLB100000002'}, {'mlb': 'MLB100000005'}, {'mlb': 'MLB100000006'}]}).get_json()
        st = {r['mlb']: r for r in d['resultados']}
        self.assertEqual(st['MLB100000001']['status'], 'OK')
        self.assertEqual(self.fake.itens['MLB100000001']['status'], 'paused')
        self.assertEqual(st['MLB100000002']['status'], 'PULADO')          # já pausado
        self.assertEqual(st['MLB100000005']['status'], 'PULADO')          # encerrado
        self.assertEqual(st['MLB100000006']['status'], 'PULADO')          # de outra conta
        self.assertEqual(self.fake.itens['MLB100000006']['status'], 'active')
        self.assertEqual(d['pausados'], 1)

    def test_exige_confirmacao_e_limite(self):
        self.assertEqual(self.post('/api/vendas/pausar', {'conta': 'ML19', 'itens': [{'mlb': 'MLB100000001'}]}).status_code, 400)
        itens = [{'mlb': 'MLB1%08d' % i} for i in range(11)]
        self.assertEqual(self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': itens}).status_code, 400)
        self.assertEqual(self.fake.puts, [])

    def test_ml_aceita_mas_nao_pausa_nao_diz_ok(self):
        self.fake.ignora = True
        d = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': [{'mlb': 'MLB100000001'}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'NAO_CONFIRMADO')

    def test_recusa_do_ml_vira_erro_e_clique_duplo_e_barrado(self):
        self.fake.recusa = True
        d = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': [{'mlb': 'MLB100000001'}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'ERRO')
        self.fake.recusa = False
        d = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': [{'mlb': 'MLB100000001'}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'PULADO')          # acabou de ser mexido


class TestTrocarSkuVendas(Base):
    def test_troca_sku_existente(self):
        d = self.post('/api/vendas/sku-trocar', {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'itens': [{'mlb': 'MLB100000001', 'sku_atual': 'GR_25kg', 'sku_novo': 'GR_25Kg_NOVO'}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'OK', d)
        self.assertEqual(self.fake.itens['MLB100000001']['seller_custom_field'], 'GR_25Kg_NOVO')

    def test_preenche_sku_de_anuncio_sem_sku(self):
        self.fake.itens['MLB200000001'] = {'id': 'MLB200000001', 'seller_id': 99, 'title': 'Sem sku', 'status': 'active', 'seller_custom_field': None, 'attributes': []}
        d = self.post('/api/vendas/sku-trocar', {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'itens': [{'mlb': 'MLB200000001', 'sku_atual': '', 'sku_novo': 'NOVO_1'}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'OK', d)
        self.assertEqual(self.fake.itens['MLB200000001']['seller_custom_field'], 'NOVO_1')

    def test_sku_mudou_no_ml_nao_troca(self):
        d = self.post('/api/vendas/sku-trocar', {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'itens': [{'mlb': 'MLB100000003', 'sku_atual': 'GR_25kg', 'sku_novo': 'X'}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'PULADO')
        self.assertEqual(self.fake.puts, [])

    def test_confirmacao_igual_vazio_e_outra_conta(self):
        base = {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU'}
        self.assertEqual(self.post('/api/vendas/sku-trocar', {'conta': 'ML19', 'itens': [{'mlb': 'MLB100000001', 'sku_atual': 'a', 'sku_novo': 'b'}]}).status_code, 400)
        for it in ({'sku_atual': 'GR_25kg', 'sku_novo': 'GR_25kg'}, {'sku_atual': 'GR_25kg', 'sku_novo': ''}):
            d = self.post('/api/vendas/sku-trocar', dict(base, itens=[dict(it, mlb='MLB100000001')])).get_json()
            self.assertEqual(d['resultados'][0]['status'], 'PULADO')
        d = self.post('/api/vendas/sku-trocar', dict(base, itens=[{'mlb': 'MLB100000006', 'sku_atual': 'GR_25kg', 'sku_novo': 'Z'}])).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'PULADO')
        self.assertEqual(self.fake.puts, [])


if __name__ == '__main__':
    unittest.main()
