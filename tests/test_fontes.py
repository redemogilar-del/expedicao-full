import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import fontes


class Resp:
    def __init__(self, dados, status=200):
        self.dados, self.status_code = dados, status
    def json(self): return self.dados
    def raise_for_status(self):
        if self.status_code >= 400: raise fontes.requests.HTTPError(str(self.status_code))


def item(mlb, status='active', seller='99', price=100, free=True, mode='me2'):
    return {'id': mlb, 'status': status, 'seller_id': int(seller), 'price': price, 'listing_type_id': 'gold_special',
            'shipping': {'mode': mode, 'logistic_type': 'fulfillment', 'free_shipping': free}}


def cotacao(valor, moeda='BRL'):
    return {'coverage': {'all_country': {'currency_id': moeda, 'list_cost': valor}}}


class TestTentaDeNovo(unittest.TestCase):
    def setUp(self):
        os.environ['EXP_SHEETS_URL'] = 'https://script.google.com/macros/s/x/exec'; os.environ['EXP_SHEETS_KEY'] = 'k'

    def test_custos_tenta_de_novo_se_a_planilha_demorar(self):
        chamadas = []
        def falso(url, json=None, **kw):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise fontes.requests.Timeout('Read timed out')
            return Resp({'ok': True, 'items': {'A': {'custo': '10'}}})
        with mock.patch.object(fontes._SESS, 'post', falso), mock.patch.object(fontes.time, 'sleep', lambda s: None):
            self.assertEqual(fontes._consultar_itens(os.environ['EXP_SHEETS_URL'], 'k', ['A']), {'A': {'custo': '10'}})
        self.assertEqual(len(chamadas), 2)

    def test_falha_duas_vezes_vira_aviso_claro(self):
        with mock.patch.object(fontes._SESS, 'post', side_effect=fontes.requests.Timeout('Read timed out')), mock.patch.object(fontes.time, 'sleep', lambda s: None):
            with self.assertRaises(fontes.FonteIndisponivel):
                fontes._consultar_itens(os.environ['EXP_SHEETS_URL'], 'k', ['A'])


class TestImpostos(unittest.TestCase):
    def setUp(self):
        os.environ['EXP_SHEETS_URL'] = 'https://script.google.com/macros/s/x/exec'; os.environ['EXP_SHEETS_KEY'] = 'k'

    def test_le_a_taxasml_de_cada_conta_sempre_na_hora(self):
        valores = {'ML19': (16.0, 18.0), 'ML44': (10.0, 12.0)}
        def falso(url, json=None, **kw):
            assert json['acao'] == 'catalogo_taxas'
            n, f = valores[json['conta']]
            return Resp({'ok': True, 'conta': json['conta'], 'imposto_embalagem_normal_pct': n, 'imposto_embalagem_full_pct': f})
        with mock.patch.object(fontes._SESS, 'post', falso):
            self.assertEqual(fontes.impostos_conta('ML19'), {'normal': 16.0, 'full': 18.0})
            self.assertEqual(fontes.impostos_conta('ML44'), {'normal': 10.0, 'full': 12.0})
            valores['ML19'] = (17.5, 18.0)                                         # alterou na planilha: vale na hora
            self.assertEqual(fontes.impostos_conta('ML19')['normal'], 17.5)

    def test_falhas_viram_erro_nunca_zero(self):
        casos = [Resp({'ok': True, 'conta': 'ML44', 'imposto_embalagem_normal_pct': 16, 'imposto_embalagem_full_pct': 18}),     # conta errada
                 Resp({'ok': True, 'conta': 'ML19', 'imposto_embalagem_normal_pct': None, 'imposto_embalagem_full_pct': 18}),     # vazio
                 Resp({'ok': True, 'conta': 'ML19', 'imposto_embalagem_normal_pct': 16, 'imposto_embalagem_full_pct': -1}),
                 Resp({'ok': False, 'erro': 'CNPJ divergente'})]
        for r in casos:
            with mock.patch.object(fontes._SESS, 'post', return_value=r), self.assertRaises(fontes.FonteIndisponivel):
                fontes.impostos_conta('ML19')


class TestRegistrarPrecos(unittest.TestCase):
    def setUp(self):
        os.environ['EXP_SHEETS_URL'] = 'https://script.google.com/macros/s/x/exec'; os.environ['EXP_SHEETS_KEY'] = 'k'

    def test_anota_e_falha_sem_derrubar(self):
        visto = {}
        def falso(url, json=None, **kw):
            visto.update(json); return Resp({'ok': True, 'aba': 'Preços atualizados ML19', 'novos': 1, 'repetidos': 0})
        with mock.patch.object(fontes._SESS, 'post', falso):
            txt = fontes.registrar_precos('ML19', [{'mlb': 'MLB1'}])
        self.assertEqual((visto['acao'], visto['conta']), ('vendas_precos', 'ML19')); self.assertIn('Preços atualizados ML19', txt)
        with mock.patch.object(fontes._SESS, 'post', return_value=Resp({'ok': False})):
            self.assertIn('não anotado', fontes.registrar_precos('ML19', []))
        with mock.patch.object(fontes._SESS, 'post', side_effect=fontes.requests.ConnectionError('x')):
            self.assertIn('não anotado', fontes.registrar_precos('ML19', []))


class TestCustos(unittest.TestCase):
    def setUp(self):
        os.environ['EXP_SHEETS_URL'] = 'https://script.google.com/macros/s/x/exec'; os.environ['EXP_SHEETS_KEY'] = 'k'

    def test_custo_ausente_ou_zero_nunca_vira_zero(self):
        resposta = {'ok': True, 'items': {'A': {'custo': 12.5}, 'B': {'custo': 0}, 'C': {'custo': '1.234,50'}}}
        with mock.patch.object(fontes._SESS, 'post', return_value=Resp(resposta)):
            r = fontes.custos(['A', 'B', 'C', 'D'])
        self.assertEqual(r, {'A': 12.5, 'B': None, 'C': 1234.5, 'D': None})

    def test_sku_com_maiuscula_diferente_acha_custo(self):
        def falso(url, json=None, **kw):
            base = {'PO_5Kg': {'custo': 7.5}}
            return Resp({'ok': True, 'items': {s: base[s] for s in json['skus'] if s in base}})
        with mock.patch.object(fontes._SESS, 'post', falso):
            self.assertEqual(fontes.custos(['PO_5kg', 'XX']), {'PO_5kg': 7.5, 'XX': None})
        def ambiguo(url, json=None, **kw):
            base = {'PO_5Kg': {'custo': 7.5}, 'PO_5KG': {'custo': 8}}
            return Resp({'ok': True, 'items': {s: base[s] for s in json['skus'] if s in base}})
        with mock.patch.object(fontes._SESS, 'post', ambiguo):
            self.assertEqual(fontes.custos(['PO_5kg']), {'PO_5kg': None})

    def test_aptos_full(self):
        with mock.patch.object(fontes._SESS, 'post', return_value=Resp({'ok': True, 'skus': [' VP4O_5m2 ', 'G£02,3_1Om2', '', None]})):
            self.assertEqual(fontes.aptos_full(), ['g£02,3_1om2', 'vp4o_5m2'])
        with mock.patch.object(fontes._SESS, 'post', return_value=Resp({'ok': False, 'error': 'Ação inválida'})):
            with self.assertRaises(fontes.FonteIndisponivel):
                fontes.aptos_full()

    def test_lotes_de_50_e_erros(self):
        chamadas = []
        def falso(url, json=None, **kw):
            chamadas.append(len(json['skus'])); return Resp({'ok': True, 'items': {s: {'custo': 1} for s in json['skus']}})
        with mock.patch.object(fontes._SESS, 'post', falso):
            fontes.custos(['S%d' % i for i in range(120)])
        self.assertEqual(chamadas, [50, 50, 20])
        with mock.patch.object(fontes._SESS, 'post', return_value=Resp({'ok': False})):
            with self.assertRaises(fontes.FonteIndisponivel):
                fontes.custos(['A'])
        os.environ['EXP_SHEETS_KEY'] = ''
        with self.assertRaises(fontes.FonteIndisponivel):
            fontes.custos(['A'])


class TestFrete(unittest.TestCase):
    def setUp(self):
        fontes._FRETES.clear()

    def com_ml(self, itens, cotas):
        def falso(tk, caminho, params=None):
            if caminho.startswith('/items/'):
                return itens[caminho.split('/')[-1]]
            if 'shipping_options' in caminho:
                return cotas[params['item_id']]
            raise AssertionError(caminho)
        return mock.patch.object(fontes, '_ml', falso)

    def test_cota_o_primeiro_anuncio_ativo(self):
        itens = {'MLB1': item('MLB1', status='closed'), 'MLB2': item('MLB2')}
        with self.com_ml(itens, {'MLB2': cotacao(7.15)}):
            r = fontes.frete_produto('ML19', 'tk', '99', ['MLB1', 'MLB2'])
        self.assertEqual((r['frete'], r['mlb']), (7.15, 'MLB2'))

    def test_anuncio_pausado_tambem_cota(self):
        with self.com_ml({'MLB1': item('MLB1', status='paused')}, {'MLB1': cotacao(11.05)}):
            r = fontes.frete_produto('ML19', 'tk', '99', ['MLB1'])
        self.assertEqual((r['frete'], r['mlb']), (11.05, 'MLB1'))

    def test_falha_nunca_vira_zero_e_diz_o_motivo(self):
        itens = {'MLB1': item('MLB1', status='closed'), 'MLB2': item('MLB2', seller='5'), 'MLB3': item('MLB3')}
        with self.com_ml(itens, {'MLB3': cotacao(None)}):
            r = fontes.frete_produto('ML19', 'tk', '99', ['MLB1', 'MLB2', 'MLB3'])
        self.assertIsNone(r['frete'])
        self.assertIn('encerrado', r['erro']); self.assertIn('não pertence', r['erro']); self.assertIn('não retornado', r['erro'])
        with self.com_ml({'MLB1': item('MLB1')}, {'MLB1': cotacao(5, moeda='USD')}):
            self.assertIsNone(fontes.frete_produto('ML19', 'tk', '99', ['MLB1'])['frete'])
        self.assertIsNone(fontes.frete_produto('ML19', 'tk', '99', [])['frete'])

    def test_cache_evita_repetir_consulta(self):
        n = []
        def falso(tk, caminho, params=None):
            n.append(caminho); return item('MLB1') if caminho.startswith('/items/') else cotacao(8.75)
        with mock.patch.object(fontes, '_ml', falso):
            fontes.frete_produto('ML19', 'tk', '99', ['MLB1']); fontes.frete_produto('ML19', 'tk', '99', ['MLB1'])
        self.assertEqual(len(n), 2)

    def test_conta_invalida_e_sem_configuracao(self):
        with self.assertRaises(fontes.FonteIndisponivel):
            fontes.token('XX')
        os.environ.pop('EXP_TOKEN_URL_ML19', None); fontes._TOKENS.clear()
        with self.assertRaises(fontes.FonteIndisponivel):
            fontes.token('ML19')


def pedido(pid, itens, status='paid'):
    return {'id': pid, 'status': status, 'order_items': [{'item': {'id': m, 'title': 'T ' + m, 'seller_sku': sku}, 'quantity': q, 'unit_price': p} for m, sku, q, p in itens]}


class TestPedidos(unittest.TestCase):
    def com_ml(self, resposta):
        return mock.patch.multiple(fontes, token=lambda c: 'tk', _vendedor=lambda c, tk: '99', _ml=lambda tk, caminho, params=None: resposta(caminho, params))

    def test_soma_por_anuncio_e_ignora_cancelados(self):
        def resp(caminho, params):
            self.assertEqual(caminho, '/orders/search'); self.assertEqual(params['order.status'], 'paid'); self.assertEqual(params['limit'], 50)
            return {'paging': {'total': 3, 'offset': 0}, 'results': [
                pedido(1, [('MLB111111111', 'A', 2, 10.0)]), pedido(2, [('MLB111111111', 'A', 1, 12.0), ('MLB222222222', '', 5, 3.0)]),
                pedido(3, [('MLB111111111', 'A', 9, 10.0)], status='cancelled')]}
        with self.com_ml(resp):
            r = fontes.pedidos_pagina('ML19', '2026-10-01', '2026-10-03')
        por = {i['mlb']: i for i in r['itens']}
        self.assertEqual((por['MLB111111111']['unidades'], por['MLB111111111']['pedidos'], por['MLB111111111']['valor']), (3, 2, 32.0))
        self.assertEqual(por['MLB222222222']['unidades'], 5)
        self.assertTrue(r['concluido']); self.assertEqual(r['proximo'], 3)

    def test_paginacao_e_mlb_invalido(self):
        def resp(caminho, params):
            return {'paging': {'total': 120}, 'results': [pedido(i, [('MLB333333333', 'S', 1, 5.0), ('XYZ', 'S', 1, 5.0)]) for i in range(50)]}
        with self.com_ml(resp):
            r = fontes.pedidos_pagina('ML19', '2026-10-01', '2026-10-03', 50)
        self.assertEqual(len(r['itens']), 1); self.assertEqual(r['proximo'], 100); self.assertFalse(r['concluido'])

    def test_falha_do_ml_vira_mensagem(self):
        def resp(caminho, params):
            raise RuntimeError('HTTP 500')
        with self.com_ml(resp):
            with self.assertRaises(fontes.FonteIndisponivel):
                fontes.pedidos_pagina('ML19', '2026-10-01', '2026-10-03')

    def test_itens_info_marca_full_e_ignora_erros(self):
        def resp(caminho, params):
            self.assertEqual(caminho, '/items')
            return [{'code': 200, 'body': {'id': 'MLB111111111', 'status': 'active', 'price': 10, 'available_quantity': 4, 'seller_custom_field': 'A', 'shipping': {'logistic_type': 'fulfillment'}, 'permalink': 'https://x'}},
                    {'code': 404, 'body': {'id': 'MLB222222222'}},
                    {'code': 200, 'body': {'id': 'MLB333333333', 'status': 'paused', 'shipping': {'logistic_type': 'xd_drop_off'}}}]
        with self.com_ml(resp):
            r = fontes.itens_info('ML19', ['MLB111111111', 'MLB222222222', 'MLB333333333', 'lixo'])
        self.assertEqual(r['MLB111111111']['rotulo'], 'Full'); self.assertEqual(r['MLB333333333']['rotulo'], 'Agência/coleta')
        self.assertNotIn('MLB222222222', r)


if __name__ == '__main__':
    unittest.main()
