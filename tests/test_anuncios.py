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


class TestPausarLimiteML(Base):
    def test_429_espera_e_tenta_de_novo(self):
        respostas = [mock.Mock(ok=False, status_code=429, text='too_many_requests', headers={}), mock.Mock(ok=False, status_code=429, text='', headers={'Retry-After': '1'})]
        original = self.fake.put

        def put(url, json=None, **kw):
            if json and 'status' in json and respostas:
                self.fake.puts.append((url, json))
                return respostas.pop(0)
            return original(url, json, **kw)
        with mock.patch.object(anuncios.fontes._SESS, 'put', side_effect=put), mock.patch.object(anuncios.time, 'sleep') as dorme:
            d = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': [{'mlb': 'MLB100000001'}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'OK')
        self.assertEqual(self.fake.itens['MLB100000001']['status'], 'paused')
        self.assertEqual(len(self.fake.puts), 3)
        self.assertTrue(dorme.called)

    def test_429_sempre_nao_altera_e_libera_nova_tentativa(self):
        original = self.fake.put

        def put(url, json=None, **kw):
            if json and 'status' in json:
                self.fake.puts.append((url, json))
                return mock.Mock(ok=False, status_code=429, text='too_many_requests', headers={})
            return original(url, json, **kw)
        with mock.patch.object(anuncios.fontes._SESS, 'put', side_effect=put), mock.patch.object(anuncios.time, 'sleep'):
            d = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': [{'mlb': 'MLB100000001'}]}).get_json()
        r = d['resultados'][0]
        self.assertEqual(r['status'], 'ERRO')
        self.assertIn('devagar', r['mensagem'])
        self.assertEqual(len(self.fake.puts), 1 + len(anuncios.ESPERAS_429))
        d = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': [{'mlb': 'MLB100000001'}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'OK')          # não ficou travado pelo anti clique duplo


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

    def test_lista_cada_variacao_do_anuncio(self):
        d = self.post('/api/vendas/anuncios-skus', {'conta': 'ML19', 'itens': [{'mlb': 'MLB100000004'}, {'mlb': 'MLB100000001'}, {'mlb': 'MLB100000006'}]}).get_json()
        por = {a['mlb']: a for a in d['anuncios']}
        self.assertEqual([(l['variacao'], l['sku']) for l in por['MLB100000004']['locais']], [(11, 'GR_P'), (12, 'GR_G'), (13, 'ZZ')])
        self.assertEqual(por['MLB100000001']['locais'][0]['variacao'], None)
        self.assertIn('erro', por['MLB100000006'])            # de outra conta
        self.assertEqual(self.fake.puts, [])

    def test_troca_varias_variacoes_do_mesmo_anuncio_com_sku_diferente(self):
        trocas = [{'variacao': 11, 'sku_atual': 'GR_P', 'sku_novo': 'NOVO_P'}, {'variacao': 12, 'sku_atual': 'GR_G', 'sku_novo': 'NOVO_G'}, {'variacao': 13, 'sku_atual': 'ZZ', 'sku_novo': 'NOVO_Z'}]
        d = self.post('/api/vendas/sku-trocar', {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'itens': [{'mlb': 'MLB100000004', 'trocas': trocas}]}).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'OK', d)
        v = {x['id']: x for x in self.fake.itens['MLB100000004']['variations']}
        self.assertEqual(v[11]['seller_custom_field'], 'NOVO_P')
        self.assertEqual(v[13]['seller_custom_field'], 'NOVO_Z')
        self.assertTrue(any(a['value_name'] == 'NOVO_G' for a in v[12]['attributes']))

    def test_variacao_errada_ou_mudada_nao_troca_e_o_resto_segue(self):
        trocas = [{'variacao': 11, 'sku_atual': 'OUTRO', 'sku_novo': 'X'}, {'variacao': 13, 'sku_atual': 'ZZ', 'sku_novo': 'NOVO_Z'}]
        d = self.post('/api/vendas/sku-trocar', {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'itens': [{'mlb': 'MLB100000004', 'trocas': trocas}]}).get_json()
        self.assertNotEqual(d['resultados'][0]['status'], 'OK')
        v = {x['id']: x for x in self.fake.itens['MLB100000004']['variations']}
        self.assertEqual(v[11]['seller_custom_field'], 'GR_P')
        self.assertEqual(v[13]['seller_custom_field'], 'NOVO_Z')


if __name__ == '__main__':
    unittest.main()


class TestReativarPorSku(Base):
    def setUp(self):
        super().setUp()
        self.fake.itens['MLB100000007'] = {'id': 'MLB100000007', 'seller_id': 99, 'title': 'Pausado', 'status': 'paused', 'seller_custom_field': 'GR_25KG', 'attributes': [], 'available_quantity': 5}
        self.fake.itens['MLB100000008'] = {'id': 'MLB100000008', 'seller_id': 99, 'title': 'Pausado sem estoque', 'status': 'paused', 'seller_custom_field': 'GR_25KG', 'attributes': [], 'available_quantity': 0}

    def reativar(self, itens, confirmacao='REATIVAR_ANUNCIOS'):
        return self.post('/api/skus/reativar', {'conta': 'ML19', 'confirmacao': confirmacao, 'itens': itens})

    def test_busca_so_pausados_com_o_sku_e_avisa_sem_estoque(self):
        d = self.post('/api/skus/buscar', {'conta': 'ML19', 'modo': 'reativar_sku', 'termo': 'gr_25kg'}).get_json()
        por = {a['mlb']: a for a in d['achados']}
        self.assertNotIn('MLB100000001', por)                 # ativo não aparece
        self.assertIn('MLB100000007', por)
        self.assertIn('sem estoque', por['MLB100000008']['aviso'])
        self.assertEqual(por['MLB100000008']['estoque'], 0)
        self.assertEqual(self.fake.puts, [])

    def test_reativa_so_pausado_com_o_sku_e_confere(self):
        r = {x['mlb']: x for x in self.reativar([{'mlb': 'MLB100000007', 'sku_visto': 'GR_25KG'}, {'mlb': 'MLB100000001', 'sku_visto': 'GR_25KG'}]).get_json()['resultados']}
        self.assertEqual(r['MLB100000007']['status'], 'OK')
        self.assertEqual(self.fake.itens['MLB100000007']['status'], 'active')
        self.assertEqual((r['MLB100000001']['status'], r['MLB100000001']['mensagem']), ('PULADO', 'Já estava ativo.'))
        self.assertEqual(self.fake.puts, [('https://api.mercadolibre.com/items/MLB100000007', {'status': 'active'})])

    def test_sku_mudou_nao_reativa(self):
        r = self.reativar([{'mlb': 'MLB100000007', 'sku_visto': 'OUTRO'}]).get_json()['resultados'][0]
        self.assertEqual(r['status'], 'PULADO')
        self.assertEqual(self.fake.puts, [])

    def test_recusa_do_ml_avisa_estoque(self):
        self.fake.recusa = True
        r = self.reativar([{'mlb': 'MLB100000008', 'sku_visto': 'GR_25KG'}]).get_json()['resultados'][0]
        self.assertEqual(r['status'], 'ERRO')
        self.assertIn('sem estoque', r['mensagem'])

    def test_exige_confirmacao(self):
        self.assertEqual(self.reativar([{'mlb': 'MLB100000007'}], confirmacao='PAUSAR_ANUNCIOS').status_code, 400)
        self.assertEqual(self.fake.puts, [])

    def test_modo_reativar_nao_troca_sku(self):
        r = self.post('/api/skus/aplicar', {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'reativar_sku', 'termo': 'GR_25KG', 'itens': [{'mlb': 'MLB100000007', 'variacao': None, 'sku_visto': 'GR_25KG', 'sku_novo': 'X'}]})
        self.assertEqual(r.status_code, 400)


class TestPausasPlanilha(Base):
    def registrar(self, **corpo):
        base = {'tipo': 'pausa', 'sku': 'GR_25KG', 'ok': 3, 'erros': 1, 'contas': ['ML19', 'ML44'], 'motivo': 'FALTA_ESTOQUE', 'previsao': '2026-12-01'}
        base.update(corpo)
        with mock.patch.object(fontes, '_sheets_post', return_value={'ok': True, 'situacao': 'Pausado', 'nova': True, 'linha': 2}) as sp:
            r = self.post('/api/skus/pausas-registrar', base)
        return r, sp

    def test_pausa_vai_para_a_planilha(self):
        r, sp = self.registrar()
        self.assertEqual(r.status_code, 200)
        enviado = sp.call_args[0][0]
        self.assertEqual({k: enviado[k] for k in ('acao', 'tipo', 'sku', 'ok', 'erros', 'contas', 'motivo', 'previsao')},
                         {'acao': 'pausas_registrar', 'tipo': 'pausa', 'sku': 'GR_25KG', 'ok': 3, 'erros': 1, 'contas': ['ML19', 'ML44'], 'motivo': 'FALTA_ESTOQUE', 'previsao': '2026-12-01'})

    def test_descontinuado_nao_leva_previsao(self):
        r, sp = self.registrar(motivo='DESCONTINUADO', previsao='2026-12-01')
        self.assertEqual(sp.call_args[0][0]['previsao'], '')

    def test_validacoes(self):
        for corpo in ({'motivo': ''}, {'motivo': 'FALTA_ESTOQUE', 'previsao': ''}, {'previsao': '01/12/2026'}, {'ok': 0, 'erros': 0}, {'ok': -1}, {'ok': True},
                      {'contas': ['XX']}, {'contas': []}, {'sku': ''}, {'tipo': 'outro'}):
            r, sp = self.registrar(**corpo)
            self.assertEqual(r.status_code, 400, corpo)
            sp.assert_not_called()

    def test_reativacao_sem_motivo(self):
        r, sp = self.registrar(tipo='reativacao', motivo='', previsao='')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sp.call_args[0][0]['tipo'], 'reativacao')

    def test_ler(self):
        with mock.patch.object(fontes, '_sheets_post', return_value={'ok': True, 'pausas': [{'sku': 'GR_25KG', 'previsao': '01/12/2026', 'situacao': 'Pausado'}, {'sku': ''}]}):
            d = self.post('/api/skus/pausas-ler', {}).get_json()
        self.assertEqual([x['sku'] for x in d['pausas']], ['GR_25KG'])
        self.assertTrue(d['somente_leitura'])

    def test_planilha_sem_o_trecho(self):
        resp = mock.Mock(status_code=200); resp.raise_for_status = lambda: None; resp.json = lambda: {'ok': False, 'erro': 'Ação desconhecida: pausas_ler'}
        with mock.patch.object(fontes._SESS, 'post', return_value=resp), mock.patch.dict(os.environ, {'EXP_SHEETS_URL': 'https://script.google.com/x', 'EXP_SHEETS_KEY': 'k'}):
            r = self.post('/api/skus/pausas-ler', {})
        self.assertIn('pausas por SKU', r.get_json()['error'])


class TestTiposDeBusca(Base):
    """Busca por SKU sem SKU novo (o SKU novo vai na linha) e pausar/reativar por SKU contém/começa com ou pelo título."""
    def setUp(self):
        super().setUp()
        self.fake.itens['MLB100000009'] = {'id': 'MLB100000009', 'seller_id': 99, 'title': 'Super Pro Fosco 3,6L Marfim', 'status': 'active', 'seller_custom_field': 'SUP\u0190R_PR0_FSC_3,6L_M@RF', 'attributes': [], 'available_quantity': 3}

    def buscar(self, **c):
        return self.post('/api/skus/buscar', dict({'conta': 'ML19'}, **c)).get_json()

    def test_sku_contem_sem_sku_novo_lista_e_troca_pelo_sku_da_linha(self):
        d = self.buscar(modo='contem', termo='PR0_FSC_3,6L', novo='', escopo='trecho')
        linha = [a for a in d['achados'] if a['mlb'] == 'MLB100000009'][0]
        self.assertEqual((linha['sku_atual'], linha['sku_novo']), ('SUP\u0190R_PR0_FSC_3,6L_M@RF', ''))
        sem = self.post('/api/skus/aplicar', {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'contem', 'termo': 'PR0_FSC_3,6L', 'novo': '', 'itens': [{'mlb': 'MLB100000009', 'variacao': None, 'sku_visto': linha['sku_atual']}]})
        self.assertEqual(sem.status_code, 400)                 # sem SKU na linha não grava
        r = self.post('/api/skus/aplicar', {'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'contem', 'termo': 'PR0_FSC_3,6L', 'novo': '',
                                            'itens': [{'mlb': 'MLB100000009', 'variacao': None, 'sku_visto': linha['sku_atual'], 'sku_novo': 'SUPER_PRO_FOSCO_3,6L_MARFIM'}]}).get_json()
        self.assertEqual(r['resultados'][0]['status'], 'OK')
        self.assertEqual(self.fake.itens['MLB100000009']['seller_custom_field'], 'SUPER_PRO_FOSCO_3,6L_MARFIM')

    def test_pausar_por_sku_contem_e_por_titulo(self):
        d = self.buscar(modo='pausar_sku', termo='pr0_fsc', como='sku_contem')
        self.assertEqual([a['mlb'] for a in d['achados']], ['MLB100000009'])
        d = self.buscar(modo='pausar_sku', termo='fosco marfim', como='tit_contem')
        self.assertEqual([a['mlb'] for a in d['achados']], ['MLB100000009'])
        d = self.buscar(modo='pausar_sku', termo='SUP', como='sku_comeca')
        self.assertIn('MLB100000009', [a['mlb'] for a in d['achados']])
        self.assertEqual(self.fake.puts, [])
        r = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'como': 'tit_contem', 'termo': 'fosco marfim', 'itens': [{'mlb': 'MLB100000009'}]}).get_json()
        self.assertEqual(r['resultados'][0]['status'], 'OK')

    def test_titulo_mudou_nao_pausa(self):
        r = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'como': 'tit_contem', 'termo': 'granito azul', 'itens': [{'mlb': 'MLB100000009'}]}).get_json()
        self.assertEqual(r['resultados'][0]['status'], 'PULADO')
        self.assertEqual(self.fake.puts, [])

    def test_reativar_por_titulo(self):
        self.fake.itens['MLB100000009']['status'] = 'paused'
        d = self.buscar(modo='reativar_sku', termo='super pro', como='tit_comeca')
        self.assertEqual([a['mlb'] for a in d['achados']], ['MLB100000009'])
        r = self.post('/api/skus/reativar', {'conta': 'ML19', 'confirmacao': 'REATIVAR_ANUNCIOS', 'como': 'tit_comeca', 'termo': 'super pro', 'itens': [{'mlb': 'MLB100000009'}]}).get_json()
        self.assertEqual(r['resultados'][0]['status'], 'OK')


class TestBuscaPorPedacos(Base):
    """"Contém" com vários pedaços separados por espaço e sem diferença pelos disfarces (@ = A, 0 = O, Ɛ = E)."""
    SKU = 'SUPƐR_PR0_FSC_3,6L_M@RF'

    def setUp(self):
        super().setUp()
        self.fake.itens['MLB100000009'] = {'id': 'MLB100000009', 'seller_id': 99, 'title': 'Super Pro Fosco 3,6L Marfim', 'status': 'active', 'seller_custom_field': self.SKU, 'attributes': [], 'available_quantity': 3}

    def test_casa_sku(self):
        c = skus.casa_sku
        self.assertTrue(c(self.SKU, 'contem', 'SUPER_PRO_   M@RF'))          # dois pedaços, um sem disfarce
        self.assertTrue(c(self.SKU, 'contem', 'marf super'))                 # qualquer ordem
        self.assertTrue(c(self.SKU, 'contem', 'FSC_3,6L'))
        self.assertFalse(c(self.SKU, 'contem', 'SUPER_PRO GELO'))            # todos os pedaços precisam estar
        self.assertTrue(c(self.SKU, 'comeca', 'super_pro'))
        self.assertFalse(c(self.SKU, 'comeca', 'PRO_FSC'))
        self.assertFalse(c(self.SKU, 'igual', 'SUPER_PRO_FSC_3,6L_MARF'))    # "igual" continua exigindo o mesmo texto
        self.assertTrue(c(self.SKU, 'igual', self.SKU.lower()))

    def test_pausar_por_dois_pedacos(self):
        d = self.post('/api/skus/buscar', {'conta': 'ML19', 'modo': 'pausar_sku', 'termo': 'SUPER_PRO_   M@RF', 'como': 'sku_contem'}).get_json()
        self.assertEqual([a['mlb'] for a in d['achados']], ['MLB100000009'])
        r = self.post('/api/vendas/pausar', {'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'como': 'sku_contem', 'termo': 'SUPER_PRO_   M@RF', 'itens': [{'mlb': 'MLB100000009'}]}).get_json()
        self.assertEqual(r['resultados'][0]['status'], 'OK')

    def test_trocar_por_pedacos_so_sku_inteiro(self):
        corpo = {'conta': 'ML19', 'modo': 'contem', 'termo': 'SUPER M@RF', 'novo': 'NOVO_X', 'escopo': 'trecho'}
        self.assertEqual(self.post('/api/skus/buscar', corpo).status_code, 400)
        d = self.post('/api/skus/buscar', dict(corpo, escopo='inteiro')).get_json()
        linha = [a for a in d['achados'] if a['mlb'] == 'MLB100000009'][0]
        self.assertEqual(linha['sku_novo'], 'NOVO_X')
        r = self.post('/api/skus/aplicar', dict(corpo, escopo='inteiro', confirmacao='TROCAR_SKU', itens=[{'mlb': 'MLB100000009', 'variacao': None, 'sku_visto': self.SKU}])).get_json()
        self.assertEqual(r['resultados'][0]['status'], 'OK')
        self.assertEqual(self.fake.itens['MLB100000009']['seller_custom_field'], 'NOVO_X')
