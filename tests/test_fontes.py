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


class TestCustos(unittest.TestCase):
    def setUp(self):
        os.environ['EXP_SHEETS_URL'] = 'https://script.google.com/macros/s/x/exec'; os.environ['EXP_SHEETS_KEY'] = 'k'

    def test_custo_ausente_ou_zero_nunca_vira_zero(self):
        resposta = {'ok': True, 'items': {'A': {'custo': 12.5}, 'B': {'custo': 0}, 'C': {'custo': '1.234,50'}}}
        with mock.patch.object(fontes._SESS, 'post', return_value=Resp(resposta)):
            r = fontes.custos(['A', 'B', 'C', 'D'])
        self.assertEqual(r, {'A': 12.5, 'B': None, 'C': 1234.5, 'D': None})

    def test_lotes_de_50_e_erros(self):
        chamadas = []
        def falso(url, json=None, **kw):
            chamadas.append(len(json['skus'])); return Resp({'ok': True, 'items': {}})
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
        itens = {'MLB1': item('MLB1', status='paused'), 'MLB2': item('MLB2')}
        with self.com_ml(itens, {'MLB2': cotacao(7.15)}):
            r = fontes.frete_produto('ML19', 'tk', '99', ['MLB1', 'MLB2'])
        self.assertEqual((r['frete'], r['mlb']), (7.15, 'MLB2'))

    def test_falha_nunca_vira_zero_e_diz_o_motivo(self):
        itens = {'MLB1': item('MLB1', status='paused'), 'MLB2': item('MLB2', seller='5'), 'MLB3': item('MLB3')}
        with self.com_ml(itens, {'MLB3': cotacao(None)}):
            r = fontes.frete_produto('ML19', 'tk', '99', ['MLB1', 'MLB2', 'MLB3'])
        self.assertIsNone(r['frete'])
        self.assertIn('não ativo', r['erro']); self.assertIn('não pertence', r['erro']); self.assertIn('não retornado', r['erro'])
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


if __name__ == '__main__':
    unittest.main()
