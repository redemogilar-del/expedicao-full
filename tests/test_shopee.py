"""Shopee dentro da Vendas do dia (conta SHOPEE): API simulada (nada de Shopee de verdade)."""
import hashlib
import hmac
import io
import os
import sys
import unittest
from unittest import mock
from urllib.parse import urlparse

os.environ['EXP_SECRET_KEY'] = 'teste'
os.environ['EXP_LOGIN_USER'] = 'admin'
from werkzeug.security import generate_password_hash
os.environ['EXP_LOGIN_PASSWORD_HASH'] = generate_password_hash('senha-de-teste')
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_mod
import fontes
import precos
import shopee

D = shopee.D
ENV = {'EXP_SHOPEE_PARTNER_ID': '123', 'EXP_SHOPEE_PARTNER_KEY': 'segredo', 'EXP_SHOPEE_TOKEN_URL': 'https://script.google.com/macros/s/X/exec', 'EXP_SHOPEE_TOKEN_KEY': 'senha'}
# tabela da planilha (bloco "Taxas Shopee"): [de, até, comissão %, taxa fixa R$, taxa fixa % do preço, média real %, itens]
TAXAS = {'ok': True, 'imposto_pct': 16, 'extra_pct': 0, 'faixas': [[0, 8, 20, 0, 50, None, 0], [8, 80, 20, 4.5, 0, None, 0], [80, 100, 14, 16, 0, None, 0],
                                                                  [100, 200, 14, 20, 0, 30, 5], [200, None, 14, 26, 0, None, 0]]}
SN_A, SN_B = '261104CDT9QASE', '261111334Y0ED2'


class Resp:
    def __init__(self, d, status=200):
        self.d, self.status_code = d, status

    def json(self):
        return self.d

    def raise_for_status(self):
        pass


class FakeShopee:
    """Loja de mentira: 2 pedidos a enviar (um com 2 itens), renda de um deles, preços e SKUs por modelo."""
    def __init__(self):
        self.precos = {('58204641896', '0'): 50.0, ('58210906325', '7'): 120.0, ('58210906325', '8'): 130.0}
        self.skus = {('58204641896', '0'): 'VP4O_5m2', ('58210906325', '7'): 'K!T_X', ('58210906325', '8'): 'K!T_Y'}
        self.status = {'58204641896': 'NORMAL', '58210906325': 'NORMAL'}
        self.promo, self.posts, self.gets = set(), [], []

    def post(self, url, params=None, json=None, **kw):
        if 'script.google.com' in url:
            assert json == {'chave': 'senha', 'acao': 'token'}
            return Resp({'ok': True, 'access_token': 'AT', 'shop_id': '999', 'expira_em_s': 14000, 'refresh_token': 'guardado-na-planilha'})
        self.posts.append((url, params, json))
        self.confere_sign(url, params)
        if url.endswith('/payment/get_escrow_detail_batch'):
            return Resp({'response': [{'escrow_detail': {'order_sn': SN_A, 'order_income': {
                'escrow_amount': 120.0, 'items': [{'item_id': 58204641896, 'model_id': 0, 'discounted_price': 50.0}, {'item_id': 58210906325, 'model_id': 7, 'discounted_price': 100.0}]}}}]})
        if url.endswith('/product/update_price'):
            pl = json['price_list'][0]
            self.precos[(str(json['item_id']), str(pl['model_id']))] = pl['original_price']
            return Resp({'response': {'success_list': [pl], 'failure_list': []}})
        if url.endswith('/product/unlist_item'):
            self.status[str(json['item_list'][0]['item_id'])] = 'UNLIST'
            return Resp({'response': {'success_list': json['item_list'], 'failure_list': []}})
        if url.endswith('/product/update_model'):
            for m in json['model']:
                self.skus[(str(json['item_id']), str(m['model_id']))] = m['model_sku']
            return Resp({'response': {}})
        if url.endswith('/product/update_item'):
            self.skus[(str(json['item_id']), '0')] = json['item_sku']
            return Resp({'response': {}})
        raise AssertionError('POST inesperado ' + url)

    def get(self, url, params=None, **kw):
        self.gets.append((url, params))
        self.confere_sign(url, params)
        if url.endswith('/order/get_order_list'):
            lista = {'READY_TO_SHIP': [{'order_sn': SN_A, 'order_status': 'READY_TO_SHIP'}], 'PROCESSED': [{'order_sn': SN_B, 'order_status': 'PROCESSED'}]}
            return Resp({'response': {'more': False, 'order_list': lista[params['order_status']]}})
        if url.endswith('/order/get_order_detail'):
            pedidos = {SN_A: {'order_sn': SN_A, 'order_status': 'READY_TO_SHIP', 'create_time': 1762800000, 'ship_by_date': 1762900000, 'item_list': [
                           {'item_id': 58204641896, 'model_id': 0, 'item_sku': 'VP4O_5m2', 'model_sku': '', 'item_name': 'Manta', 'model_name': '', 'model_quantity_purchased': 1, 'model_discounted_price': 50.0},
                           {'item_id': 58210906325, 'model_id': 7, 'item_sku': '', 'model_sku': 'K!T_X', 'item_name': 'Kit', 'model_name': 'Cinza', 'model_quantity_purchased': 2, 'model_discounted_price': 50.0}]},
                       SN_B: {'order_sn': SN_B, 'order_status': 'PROCESSED', 'create_time': 1762800000, 'item_list': [
                           {'item_id': 58210907800, 'model_id': 0, 'item_sku': 'SEM_RENDA', 'item_name': 'Outro', 'model_quantity_purchased': 1, 'model_discounted_price': 30.0}]},
                       '261101ZZZZZZZZ': {'order_sn': '261101ZZZZZZZZ', 'order_status': 'COMPLETED', 'create_time': 1762800000, 'item_list': []}}
            return Resp({'response': {'order_list': [pedidos[s] for s in params['order_sn_list'].split(',') if s in pedidos]}})
        if url.endswith('/product/get_model_list'):
            item = str(params['item_id'])
            modelos = [{'model_id': int(m), 'model_sku': self.skus.get((i, m), ''), 'tier_index': [0], 'promotion_id': 1 if (i, m) in self.promo else 0,
                        'price_info': [{'current_price': p, 'original_price': p}]} for (i, m), p in self.precos.items() if i == item and m != '0']
            return Resp({'response': {'model': modelos, 'tier_variation': [{'name': 'Cor', 'option_list': [{'option': 'Cinza'}]}] if modelos else []}})
        if url.endswith('/product/get_item_base_info'):
            item = str(params['item_id_list'])
            p = self.precos.get((item, '0'), 0)
            return Resp({'response': {'item_list': [{'item_id': int(item), 'item_name': 'Anúncio ' + item, 'item_status': self.status.get(item, 'NORMAL'),
                                                    'item_sku': self.skus.get((item, '0'), ''), 'price_info': [{'current_price': p, 'original_price': p}]}]}})
        raise AssertionError('GET inesperado ' + url)

    def confere_sign(self, url, params):
        caminho = urlparse(url).path
        base = '123%s%s%s%s' % (caminho, params['timestamp'], params['access_token'], params['shop_id'])
        assert params['sign'] == hmac.new(b'segredo', base.encode(), hashlib.sha256).hexdigest(), 'assinatura errada'

    def patches(self):
        return [mock.patch.dict(os.environ, ENV), mock.patch.object(shopee._SESS, 'post', self.post), mock.patch.object(shopee._SESS, 'get', self.get),
                mock.patch.object(fontes, '_sheets_post', lambda corpo, **kw: TAXAS if corpo.get('acao') == 'shopee_taxas' else {'ok': True})]


def com(fake):
    from contextlib import ExitStack
    pilha = ExitStack()
    for p in fake.patches():
        pilha.enter_context(p)
    shopee._TOKEN.update(valor=None, ate=0.0)
    shopee._TAXAS.update(dados=None, em=0.0)
    shopee._RECENTES.clear()
    return pilha


def tx():
    with mock.patch.object(fontes, '_sheets_post', lambda corpo, **kw: TAXAS):
        shopee._TAXAS.update(dados=None, em=0.0)
        return shopee.taxas()


class TestRegra(unittest.TestCase):
    def test_taxa_da_planilha_e_preco_alvo(self):
        t = tx()
        self.assertEqual(shopee.tarifa(D('16.29'), t)[0], D('7.76'))           # 20% + R$ 4,50 (a conta do teste real)
        self.assertEqual(shopee.tarifa(D('5'), t)[0], D('3.50'))               # até R$ 8: 20% + 50% do preço
        self.assertEqual(shopee.tarifa(D('150'), t)[0], D('45.00'))            # média real 30% > tabela 14% + 20
        p = shopee.preco_alvo(D('5.50'), D('50'), D('16'), t)
        self.assertEqual(p, D('19.93'))                                         # mesmo preço que o ML Hub aplicou
        self.assertGreaterEqual(shopee.margem_no_preco(D('5.50'), p, D('16'), t), D('50'))
        with self.assertRaises(shopee.Bloqueio):
            shopee.preco_alvo(None, D('20'), D('16'), t)

    def test_numero_e_chave(self):
        self.assertTrue(shopee.numero_valido(SN_A))
        self.assertFalse(shopee.numero_valido('2000019232343594'))
        self.assertEqual(shopee.separar('58210906325_7'), ('58210906325', '7'))
        with self.assertRaises(shopee.Bloqueio):
            shopee.separar('MLB123')

    def test_sem_configuracao_nao_chama_nada(self):
        with mock.patch.dict(os.environ, {k: '' for k in ENV}):
            self.assertFalse(shopee.configurado())
            with self.assertRaises(fontes.FonteIndisponivel):
                shopee.token()


class TestRotas(unittest.TestCase):
    def setUp(self):
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)

    def pedidos(self, fake, **extra):
        corpo = dict({'conta': 'SHOPEE', 'de': '2026-11-01', 'ate': '2026-11-18', 'offset': 0, 'somente_a_enviar': True, 'etapa': 'todos', 'prazo': ''}, **extra)
        with com(fake):
            return self.c.post('/api/vendas/pedidos', json=corpo).get_json()

    def test_aba_antiga_saiu(self):
        self.assertEqual(self.c.get('/shopee').status_code, 404)
        self.assertNotIn('/shopee"', self.c.get('/vendas').get_data(as_text=True))
        self.assertIn('Todas (ML19, ML44 e Shopee)', self.c.get('/vendas').get_data(as_text=True))

    def test_pedidos_entram_na_vendas_do_dia(self):
        d = self.pedidos(FakeShopee())
        self.assertTrue(d['ok'], d); self.assertTrue(d['concluido'])
        a = [l for l in d['itens'] if l['pedido'] == SN_A]
        self.assertEqual([(l['mlb'], l['anuncio'], l['sku'], l['venda'], l['tarifa'], l['frete'], l['grupo']) for l in a],
                         [('58204641896_0', '58204641896', 'VP4O_5m2', 50.0, 10.0, 0.0, 'a_enviar'), ('58210906325_7', '58210906325', 'K!T_X', 100.0, 20.0, 0.0, 'a_enviar')])
        b = [l for l in d['itens'] if l['pedido'] == SN_B][0]
        self.assertIsNone(b['tarifa'])                                         # sem renda: nunca vira zero
        self.assertTrue(a[0]['limite_dt'] and 'enviar até' in a[0]['status_rotulo'])
        # etapa "só prontos" = etiqueta gerada (PROCESSED)
        self.assertEqual({l['pedido'] for l in self.pedidos(FakeShopee(), etapa='prontos')['itens']}, {SN_B})
        self.assertEqual(self.pedidos(FakeShopee(), etapa='aguardando')['itens'], [])

    def test_calculo_impostos_e_sem_renda(self):
        d = self.pedidos(FakeShopee())
        with com(FakeShopee()):
            imp = self.c.post('/api/vendas/impostos', json={'contas': ['SHOPEE']}).get_json()
        self.assertEqual(imp['impostos']['SHOPEE'], {'normal': 16.0, 'full': 16.0})
        cad = {'VP4O_5m2': {'custo': 20, 'margem': 30, 'margem_full': 30}, 'K!T_X': {'custo': 30, 'margem': 25, 'margem_full': 25}}
        r = self.c.post('/api/vendas/calcular', json={'linhas': d['itens'], 'cadastro': cad, 'parametros': {'imposto_pct': '', 'margem_padrao': 20, 'impostos': imp['impostos']}}).get_json()
        por = {l['sku']: l for l in r['linhas']}
        self.assertEqual((por['VP4O_5m2']['lucro'], por['VP4O_5m2']['classe'], por['VP4O_5m2']['meta']), (12.0, 'dentro', 30.0))   # 50 − 10 − 8 − 20; margem do ML
        self.assertEqual(por['VP4O_5m2']['anuncio'], '58204641896')
        self.assertIn('renda da Shopee', por['SEM_RENDA']['pendencias'])

    def test_preco_alvo_e_aplicar(self):
        fake = FakeShopee()
        item = {'mlb': '58204641896_0', 'custo': 30, 'meta': 25, 'imposto_pct': 16}
        with com(fake):
            a = self.c.post('/api/vendas/preco-alvo', json={'conta': 'SHOPEE', 'parametros': {'imposto_pct': ''}, 'itens': [item]}).get_json()['resultados']['58204641896_0']
            self.assertEqual(a['acao'], 'subir', a)
            self.assertEqual(fake.posts, [])                                   # calcular não altera nada
            corpo = {'conta': 'SHOPEE', 'parametros': {'imposto_pct': ''}, 'liberar_acima_limite': True, 'itens': [dict(item, preco_atual_visto=50, preco_novo_visto=a['preco_novo'])]}
            self.assertEqual(self.c.post('/api/vendas/preco-aplicar', json=corpo).status_code, 400)          # sem confirmação
            corpo['confirmacao'] = precos.CONFIRMACAO
            r = self.c.post('/api/vendas/preco-aplicar', json=corpo).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'OK', r)
            self.assertEqual(fake.precos[('58204641896', '0')], a['preco_novo'])
            r = self.c.post('/api/vendas/preco-aplicar', json=corpo).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'PULADO')                            # clique duplo não aplica de novo

    def test_aplicar_recusa_preco_que_mudou_e_promocao(self):
        fake = FakeShopee()
        with com(fake):
            item = {'mlb': '58210906325_7', 'custo': 100, 'meta': 30, 'imposto_pct': 16, 'preco_atual_visto': 110, 'preco_novo_visto': 200}
            r = self.c.post('/api/vendas/preco-aplicar', json={'conta': 'SHOPEE', 'confirmacao': precos.CONFIRMACAO, 'parametros': {}, 'liberar_acima_limite': True, 'itens': [item]}).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'PULADO'); self.assertIn('mudou', r['mensagem'])
            fake.promo.add(('58210906325', '7'))
            a = self.c.post('/api/vendas/preco-alvo', json={'conta': 'SHOPEE', 'parametros': {}, 'itens': [{'mlb': '58210906325_7', 'custo': 100, 'meta': 30, 'imposto_pct': 16}]}).get_json()['resultados']['58210906325_7']
            self.assertEqual(a['acao'], 'bloqueado'); self.assertIn('promoção', a['mensagem'])
        self.assertFalse(any(u.endswith('update_price') for u, _p, _j in fake.posts))

    def test_pausar(self):
        fake = FakeShopee()
        with com(fake):
            self.assertEqual(self.c.post('/api/vendas/pausar', json={'conta': 'SHOPEE', 'itens': [{'mlb': '58204641896'}]}).status_code, 400)   # sem confirmação
            corpo = {'conta': 'SHOPEE', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': [{'mlb': '58204641896'}]}
            r = self.c.post('/api/vendas/pausar', json=corpo).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'OK', r)
            self.assertEqual(fake.status['58204641896'], 'UNLIST')
            r = self.c.post('/api/vendas/pausar', json=corpo).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'PULADO')                            # já fora do ar: não mexe

    def test_trocar_sku(self):
        fake = FakeShopee()
        with com(fake):
            d = self.c.post('/api/vendas/anuncios-skus', json={'conta': 'SHOPEE', 'itens': [{'mlb': '58210906325'}]}).get_json()
            self.assertEqual([(l['variacao'], l['sku']) for l in d['anuncios'][0]['locais']], [('7', 'K!T_X'), ('8', 'K!T_Y')])
            self.assertEqual(fake.posts, [])
            corpo = {'conta': 'SHOPEE', 'confirmacao': 'TROCAR_SKU', 'itens': [{'mlb': '58210906325', 'trocas': [{'variacao': '7', 'sku_atual': 'K!T_X', 'sku_novo': 'KIT_NOVO'}]}]}
            r = self.c.post('/api/vendas/sku-trocar', json=corpo).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'OK', r)
            self.assertEqual((fake.skus[('58210906325', '7')], fake.skus[('58210906325', '8')]), ('KIT_NOVO', 'K!T_Y'))
            r = self.c.post('/api/vendas/sku-trocar', json=corpo).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'PULADO'); self.assertIn('mudou', r['mensagem'])          # SKU de hoje não é mais o visto

    def test_upseller_por_numero_e_diagnostico(self):
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(['Nº de Pedido da Plataforma', 'Nome da Loja no UpSeller', 'Estado do Pedido'])
        ws.append([SN_A, 'Shopee Rede Mogilar', 'Em Processo']); ws.append(['2000019232343594', 'Mercado Livre 19', 'Em Processo']); ws.append(['abc', 'Outra', 'x'])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        d = self.c.post('/api/vendas/upseller-ler', data={'planilha': (buf, 'Export.xlsx')}, content_type='multipart/form-data').get_json()
        self.assertEqual([(p['pedido'], p['conta']) for p in d['pedidos']], [(SN_A, 'SHOPEE'), ('2000019232343594', 'ML19')])
        self.assertEqual(d['outras_lojas'], 1)
        fake = FakeShopee()
        with com(fake):
            r = self.c.post('/api/vendas/pedidos-por-numero', json={'pedidos': [{'pedido': SN_A, 'conta': 'SHOPEE'}, {'pedido': '261101ZZZZZZZZ', 'conta': 'SHOPEE'}]}).get_json()
            self.assertEqual({(l['pedido'], l['pedido_upseller']) for l in r['itens']}, {(SN_A, SN_A)})
            self.assertEqual(r['ignorados'], [])                              # pedido concluído também é venda (entra como já enviado)
            g = self.c.post('/api/vendas/diagnostico-pedidos', json={'somente_a_enviar': True, 'etapa': 'prontos', 'pedidos': [{'pedido': SN_A, 'conta': 'SHOPEE'}, {'pedido': '261101ZZZZZZZZ', 'conta': ''}]}).get_json()
            mot = {x['pedido']: x['motivo'] for x in g['resultados']}
            self.assertIn('Fora da etapa', mot[SN_A]); self.assertIn('não falta enviar', mot['261101ZZZZZZZZ'])

    def test_etiquetas_aceitam_shopee(self):
        with mock.patch.object(fontes, 'etiquetas_registrar', return_value={'gravados': 1, 'atualizados': 0}) as reg, \
                mock.patch.object(fontes, 'etiquetas_ler', return_value={SN_A: {'situacao': 'GERADA', 'data': 'x'}}):
            r = self.c.post('/api/vendas/etiquetas-marcar', json={'situacao': 'GERADA', 'registros': [{'conta': 'SHOPEE', 'pedido': SN_A, 'sku': 'VP4O_5m2', 'quantidade': 1}]})
            self.assertEqual(r.status_code, 200); self.assertEqual(reg.call_args[0][0][0]['conta'], 'SHOPEE')
            self.assertEqual(self.c.post('/api/vendas/etiquetas-ler', json={'conta': 'SHOPEE', 'pedidos': [SN_A]}).get_json()['etiquetas'][SN_A]['situacao'], 'GERADA')


if __name__ == '__main__':
    unittest.main()
