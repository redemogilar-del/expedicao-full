"""Aba Shopee: API simulada (nada de Shopee de verdade)."""
import hashlib
import hmac
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
import shopee

D = shopee.D
ENV = {'EXP_SHOPEE_PARTNER_ID': '123', 'EXP_SHOPEE_PARTNER_KEY': 'segredo', 'EXP_SHOPEE_TOKEN_URL': 'https://script.google.com/macros/s/X/exec', 'EXP_SHOPEE_TOKEN_KEY': 'senha'}


class Resp:
    def __init__(self, d, status=200):
        self.d, self.status_code = d, status

    def json(self):
        return self.d

    def raise_for_status(self):
        pass


class FakeShopee:
    """Loja de mentira: 2 pedidos a enviar (um com 2 itens), renda de um deles, preços por modelo."""
    def __init__(self):
        self.precos = {('58204641896', '0'): 50.0, ('58210906325', '7'): 120.0}
        self.promo, self.posts, self.gets, self.recusa = set(), [], [], False

    def post(self, url, params=None, json=None, **kw):
        if 'script.google.com' in url:
            assert json == {'chave': 'senha', 'acao': 'token'}
            return Resp({'ok': True, 'access_token': 'AT', 'shop_id': '999', 'expira_em_s': 14000})
        self.posts.append((url, params, json))
        self.confere_sign(url, params)
        if url.endswith('/payment/get_escrow_detail_batch'):
            return Resp({'response': [{'escrow_detail': {'order_sn': 'A1', 'order_income': {
                'escrow_amount': 120.0, 'commission_fee': 22.0, 'service_fee': 4.0, 'voucher_from_seller': 2.0, 'seller_discount': 0, 'final_shipping_fee': -2.0,
                'items': [{'item_id': 58204641896, 'model_id': 0, 'discounted_price': 50.0}, {'item_id': 58210906325, 'model_id': 7, 'discounted_price': 100.0}]}}}]})
        if url.endswith('/product/update_price'):
            if self.recusa:
                return Resp({'response': {'failure_list': [{'model_id': json['price_list'][0]['model_id'], 'failed_reason': 'preço inválido'}]}})
            pl = json['price_list'][0]
            self.precos[(str(json['item_id']), str(pl['model_id']))] = pl['original_price']
            return Resp({'response': {'success_list': [pl], 'failure_list': []}})
        raise AssertionError('POST inesperado ' + url)

    def get(self, url, params=None, **kw):
        self.gets.append((url, params))
        self.confere_sign(url, params)
        if url.endswith('/order/get_order_list'):
            lista = {'READY_TO_SHIP': [{'order_sn': 'A1', 'order_status': 'READY_TO_SHIP'}], 'PROCESSED': [{'order_sn': 'B2', 'order_status': 'PROCESSED'}]}
            return Resp({'response': {'more': False, 'order_list': lista[params['order_status']]}})
        if url.endswith('/order/get_order_detail'):
            assert params['order_sn_list'] in ('A1,B2', 'B2,A1')
            return Resp({'response': {'order_list': [
                {'order_sn': 'A1', 'create_time': 1762800000, 'ship_by_date': 1762900000, 'item_list': [
                    {'item_id': 58204641896, 'model_id': 0, 'item_sku': 'VP4O_5m2', 'model_sku': '', 'item_name': 'Manta', 'model_name': '', 'model_quantity_purchased': 1, 'model_discounted_price': 50.0},
                    {'item_id': 58210906325, 'model_id': 7, 'item_sku': '', 'model_sku': 'K!T_X', 'item_name': 'Kit', 'model_name': 'Cinza', 'model_quantity_purchased': 2, 'model_discounted_price': 50.0}]},
                {'order_sn': 'B2', 'create_time': 1762800000, 'item_list': [
                    {'item_id': 58210907800, 'model_id': 0, 'item_sku': 'SEM_RENDA', 'item_name': 'Outro', 'model_quantity_purchased': 1, 'model_discounted_price': 30.0}]}]}})
        if url.endswith('/product/get_model_list'):
            item = str(params['item_id'])
            modelos = [{'model_id': int(m), 'has_promotion': (i, m) in self.promo, 'price_info': [{'current_price': p, 'original_price': p}]}
                       for (i, m), p in self.precos.items() if i == item and m != '0']
            return Resp({'response': {'model': modelos}})
        if url.endswith('/product/get_item_base_info'):
            item = str(params['item_id_list'])
            p = self.precos[(item, '0')]
            return Resp({'response': {'item_list': [{'item_id': int(item), 'price_info': [{'current_price': p, 'original_price': p}]}]}})
        raise AssertionError('GET inesperado ' + url)

    def confere_sign(self, url, params):
        caminho = url.split('shopee.com.br')[1]
        base = '123%s%s%s%s' % (caminho, params['timestamp'], params['access_token'], params['shop_id'])
        assert params['sign'] == hmac.new(b'segredo', base.encode(), hashlib.sha256).hexdigest(), 'assinatura errada'

    def patches(self):
        return [mock.patch.dict(os.environ, ENV), mock.patch.object(shopee._SESS, 'post', self.post), mock.patch.object(shopee._SESS, 'get', self.get)]


def com(fake):
    from contextlib import ExitStack
    pilha = ExitStack()
    for p in fake.patches():
        pilha.enter_context(p)
    shopee._TOKEN.update(valor=None, ate=0.0)
    shopee._RECENTES.clear()
    return pilha


class TestRegra(unittest.TestCase):
    def test_tabela_e_preco_alvo(self):
        self.assertEqual(shopee.tarifa_tabela(D('79.99')), (D('20'), D('4')))
        self.assertEqual(shopee.tarifa_tabela(D('80')), (D('14'), D('16')))
        self.assertEqual(shopee.tarifa_tabela(D('250')), (D('14'), D('26')))
        p = shopee.preco_alvo(D('30'), D('25'), D('8'))
        self.assertGreaterEqual(shopee.margem_no_preco(D('30'), p, D('8')), D('25'))
        self.assertLess(shopee.margem_no_preco(D('30'), p - D('0.01'), D('8')), D('25'))
        # perto da virada de faixa: o menor preço que atinge a margem, mesmo que a faixa de baixo não sirva
        p = shopee.preco_alvo(D('60'), D('20'), D('6'))
        self.assertGreaterEqual(shopee.margem_no_preco(D('60'), p, D('6')), D('20'))
        with self.assertRaises(shopee.Bloqueio):
            shopee.preco_alvo(None, D('20'), D('6'))

    def test_sem_configuracao_nao_chama_nada(self):
        with mock.patch.dict(os.environ, {k: '' for k in ENV}):
            self.assertFalse(shopee.configurado())
            with self.assertRaises(fontes.FonteIndisponivel):
                shopee.token()

    def test_linhas_dividem_o_liquido_pelo_valor_do_item(self):
        fake = FakeShopee()
        with com(fake):
            pedidos = shopee.pedidos_a_enviar(15)
            det, renda = shopee.detalhes([s for s, _ in pedidos]), shopee.rendas([s for s, _ in pedidos])
        linhas = shopee.linhas_dos_pedidos(pedidos, det, renda)
        a = [l for l in linhas if l['pedido'] == 'A1']
        self.assertEqual([(l['sku'], l['venda'], l['liquido'], l['tarifa'], l['frete']) for l in a],
                         [('VP4O_5m2', 50.0, 40.0, 10.0, 0.0), ('K!T_X', 100.0, 80.0, 20.0, 0.0)])
        self.assertEqual(a[1]['comissao'], 14.67)
        b = [l for l in linhas if l['pedido'] == 'B2'][0]
        self.assertIsNone(b['tarifa'])                                   # sem renda: nunca vira zero
        self.assertIn('não informou', b['obs'])


class TestRotas(unittest.TestCase):
    def setUp(self):
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)

    def test_exige_login(self):
        c = app_mod.app.test_client()
        for rota in ('/api/shopee/pedidos', '/api/shopee/calcular', '/api/shopee/preco-alvo', '/api/shopee/preco-aplicar'):
            self.assertEqual(c.post(rota).status_code, 401)

    def test_pagina_e_menu(self):
        r = self.c.get('/shopee')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Shopee', r.get_data(as_text=True))

    def test_pedidos_e_calculo_com_margem_shopee(self):
        fake = FakeShopee()
        with com(fake):
            d = self.c.post('/api/shopee/pedidos', json={'dias': 15}).get_json()
        self.assertTrue(d['ok']); self.assertTrue(d['somente_leitura'])
        self.assertEqual((d['pedidos'], d['sem_renda']), (2, 1))
        cad = {'VP4O_5m2': {'custo': 20, 'margem': 30, 'margem_full': 30, 'margem_shopee': 50}, 'K!T_X': {'custo': 30, 'margem': 25, 'margem_full': 25, 'margem_shopee': None}}
        r = self.c.post('/api/shopee/calcular', json={'linhas': d['linhas'], 'cadastro': cad,
                                                      'parametros': {'imposto_pct': None, 'margem_padrao': 20, 'impostos': {'ML19': {'normal': 6, 'full': 4}, 'ML44': {'normal': 8, 'full': 4}}}}).get_json()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['imposto_pct'], 8.0)                         # o maior "normal" das contas do ML
        por = {l['sku']: l for l in r['linhas']}
        vp = por['VP4O_5m2']
        self.assertEqual((vp['lucro'], vp['margem'], vp['meta'], vp['meta_origem'], vp['classe']), (16.0, 80.0, 50.0, 'Shopee', 'dentro'))   # 50 − 10 − 4 − 20
        kit = por['K!T_X']
        self.assertEqual(kit['meta_origem'], 'ML (sem margem Shopee)')
        self.assertEqual(por['SEM_RENDA']['classe'], 'sem_dados')

    def test_preco_alvo_e_aplicar(self):
        fake = FakeShopee()
        item = {'mlb': '58204641896', 'modelo': '0', 'custo': 30, 'meta': 25}
        with com(fake):
            a = self.c.post('/api/shopee/preco-alvo', json={'parametros': {'imposto_pct': 8}, 'itens': [item]}).get_json()['resultados'][0]
            self.assertEqual(a['acao'], 'subir', a)
            self.assertEqual(fake.posts, [])                              # calcular não altera nada
            # sem confirmação: recusa
            r = self.c.post('/api/shopee/preco-aplicar', json={'parametros': {'imposto_pct': 8}, 'itens': [dict(item, preco_atual_visto=50, preco_novo_visto=a['preco_novo'])]})
            self.assertEqual(r.status_code, 400)
            corpo = {'confirmacao': shopee.CONFIRMACAO, 'parametros': {'imposto_pct': 8}, 'liberar_acima_limite': True,
                     'itens': [dict(item, preco_atual_visto=50, preco_novo_visto=a['preco_novo'])]}
            r = self.c.post('/api/shopee/preco-aplicar', json=corpo).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'OK', r)
            self.assertEqual(fake.precos[('58204641896', '0')], a['preco_novo'])
            # clique duplo: não aplica de novo
            r = self.c.post('/api/shopee/preco-aplicar', json=corpo).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'PULADO')

    def test_aplicar_recusa_preco_que_mudou_e_promocao(self):
        fake = FakeShopee()
        with com(fake):
            item = {'mlb': '58210906325', 'modelo': '7', 'custo': 100, 'meta': 30, 'preco_atual_visto': 110, 'preco_novo_visto': 200}
            r = self.c.post('/api/shopee/preco-aplicar', json={'confirmacao': shopee.CONFIRMACAO, 'parametros': {'imposto_pct': 8}, 'liberar_acima_limite': True, 'itens': [item]}).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'PULADO'); self.assertIn('mudou', r['mensagem'])
            fake.promo.add(('58210906325', '7'))
            a = self.c.post('/api/shopee/preco-alvo', json={'parametros': {'imposto_pct': 8}, 'itens': [{'mlb': '58210906325', 'modelo': '7', 'custo': 100, 'meta': 30}]}).get_json()['resultados'][0]
            self.assertEqual(a['acao'], 'bloqueado'); self.assertIn('promoção', a['mensagem'])
        self.assertFalse(any(u.endswith('update_price') for u, _p, _j in fake.posts))


if __name__ == '__main__':
    unittest.main()
