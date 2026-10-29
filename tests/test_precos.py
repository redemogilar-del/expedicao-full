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
import precos

D = precos.D


class FakeML:
    """ML de mentira: tarifa 16% do preço (+ R$ 6,50 fixos abaixo de R$ 79), frete fixo, preços guardados por anúncio."""
    def __init__(self, preco=40.0, frete=12.0, status='active', variacoes=None, tipo='gold_special', dono=99):
        self.preco, self.frete, self.status, self.variacoes, self.tipo, self.dono, self.puts, self.recusa = preco, frete, status, variacoes, tipo, dono, [], False

    def item(self, mlb):
        d = {'id': mlb, 'seller_id': self.dono, 'status': self.status, 'price': self.preco, 'listing_type_id': self.tipo, 'category_id': 'MLB1', 'currency_id': 'BRL',
             'shipping': {'mode': 'me2', 'free_shipping': True, 'logistic_type': 'xd_drop_off'}}
        if self.variacoes is not None:
            d['variations'] = [{'id': i, 'price': p} for i, p in enumerate(self.variacoes, 1)]
        return d

    def ml(self, tk, caminho, params=None):
        if caminho.startswith('/items/'):
            return self.item(caminho.split('/')[-1])
        if caminho == '/sites/MLB/listing_prices':
            p = float(params['price']); return {'listing_type_id': params['listing_type_id'], 'sale_fee_amount': round(p * 0.16 + (6.5 if p < 79 else 0), 2)}
        if caminho.endswith('/shipping_options/free'):
            return {'coverage': {'all_country': {'currency_id': 'BRL', 'list_cost': self.frete}}}
        raise RuntimeError('rota inesperada ' + caminho)

    def put(self, url, json=None, **kw):
        self.puts.append((url, json))
        r = mock.Mock(ok=not self.recusa, status_code=400 if self.recusa else 200, text='recusado')
        if not self.recusa:
            self.preco = json['price'] if 'price' in json else json['variations'][0]['price']
            if self.variacoes is not None:
                self.variacoes = [self.preco for _ in self.variacoes]
        return r

    def patches(self):
        return [mock.patch.object(fontes, '_ml', self.ml), mock.patch.object(fontes._SESS, 'put', self.put), mock.patch.object(fontes, 'token', lambda c: 't'),
                mock.patch.object(fontes, '_vendedor', lambda c, t: '99'), mock.patch.object(fontes, 'atualizar_referencia', lambda conta, itens: {i['mlb']: 'ok' for i in itens})]


class TestCalculoPreco(unittest.TestCase):
    def analisar(self, fake, custo=30, meta=25, imposto=8):
        with mock.patch.object(fontes, '_ml', fake.ml):
            return precos.analisar('t', '99', fake.item('MLB123456789'), custo, meta, imposto)

    def test_sobe_ate_a_margem_com_tarifa_e_frete_no_preco_novo(self):
        r = self.analisar(FakeML())
        self.assertEqual(r['acao'], 'subir')
        p = D(str(r['preco_novo']))
        def margem(x):                                                              # tarifa 16% + R$ 6,50 fixos abaixo de R$ 79; frete 12; imposto 8%; custo 30
            return (x - (x * D('0.16') + (D('6.5') if x < 79 else 0)) - 12 - x * D('0.08') - 30) / 30 * 100
        self.assertGreaterEqual(margem(p), 25)
        self.assertLess(margem(p - D('0.01')), 25)                                  # um centavo a menos já não bateria (menor preço)
        self.assertGreater(r['margem_atual'], -100); self.assertLess(r['margem_atual'], 25)

    def test_ja_atinge_a_margem_nao_altera(self):
        r = self.analisar(FakeML(preco=200.0))
        self.assertEqual((r['acao'], r['preco_novo']), ('ok', None))

    def test_bloqueios_com_motivo(self):
        for fake, trecho in ((FakeML(status='closed'), 'closed'), (FakeML(tipo='gold'), 'Tipo de anúncio'), (FakeML(dono=1), 'não pertence'),
                             (FakeML(variacoes=[40.0, 45.0]), 'variação')):
            with self.assertRaises(precos.Bloqueio) as cm:
                self.analisar(fake)
            self.assertIn(trecho, str(cm.exception))
        for custo, meta in ((None, 25), (0, 25), (30, None)):
            with self.assertRaises(precos.Bloqueio):
                self.analisar(FakeML(), custo=custo, meta=meta)

    def test_falha_de_consulta_bloqueia_e_nunca_vira_zero(self):
        fake = FakeML()
        def quebra(tk, caminho, params=None):
            if caminho.endswith('/shipping_options/free'):
                raise RuntimeError('HTTP 500')
            return fake.ml(tk, caminho, params)
        with mock.patch.object(fontes, '_ml', quebra), self.assertRaises(precos.Bloqueio) as cm:
            precos.analisar('t', '99', fake.item('MLB123456789'), 30, 25, 8)
        self.assertIn('frete', str(cm.exception))

    def test_aumento_grande_exige_liberacao(self):
        r = self.analisar(FakeML(preco=10.0))
        self.assertTrue(r['requer_liberacao'])


class TestEndpointsPreco(unittest.TestCase):
    def setUp(self):
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)
        precos._RECENTES.clear()

    def com(self, fake):
        from contextlib import ExitStack
        pilha = ExitStack()
        for p in fake.patches():
            pilha.enter_context(p)
        return pilha

    def corpo_alvo(self, **kw):
        return dict({'conta': 'ML19', 'parametros': {'imposto_pct': 8}, 'itens': [{'mlb': 'MLB123456789', 'custo': 30, 'meta': 25}]}, **kw)

    def test_exige_login(self):
        c = app_mod.app.test_client()
        for rota in ('/api/vendas/preco-alvo', '/api/vendas/preco-aplicar'):
            self.assertEqual(c.post(rota).status_code, 401)

    def test_preco_alvo_somente_leitura(self):
        fake = FakeML(preco=60.0)
        with self.com(fake):
            d = self.c.post('/api/vendas/preco-alvo', json=self.corpo_alvo()).get_json()
        r = d['resultados']['MLB123456789']
        self.assertTrue(d['somente_leitura']); self.assertEqual(r['acao'], 'subir'); self.assertEqual(fake.puts, [])
        with self.com(FakeML(status='closed')):
            r = self.c.post('/api/vendas/preco-alvo', json=self.corpo_alvo()).get_json()['resultados']['MLB123456789']
        self.assertEqual(r['acao'], 'bloqueado')
        self.assertEqual(self.c.post('/api/vendas/preco-alvo', json=dict(self.corpo_alvo(), itens=[{'mlb': 'http://x', 'custo': 1, 'meta': 1}])).status_code, 400)
        self.assertEqual(self.c.post('/api/vendas/preco-alvo', json=self.corpo_alvo(conta='XX')).status_code, 400)

    def aplicar(self, fake, visto_atual=None, visto_novo=None, **extra):
        with self.com(fake):
            a = self.c.post('/api/vendas/preco-alvo', json=self.corpo_alvo()).get_json()['resultados']['MLB123456789']
            item = {'mlb': 'MLB123456789', 'custo': 30, 'meta': 25, 'preco_atual_visto': visto_atual or a['preco_atual'], 'preco_novo_visto': visto_novo or a['preco_novo']}
            corpo = dict({'conta': 'ML19', 'parametros': {'imposto_pct': 8}, 'itens': [item], 'confirmacao': precos.CONFIRMACAO}, **extra)
            return a, self.c.post('/api/vendas/preco-aplicar', json=corpo)

    def test_aplicar_altera_confere_e_atualiza_planilha(self):
        fake = FakeML(preco=70.0)
        a, r = self.aplicar(fake)
        d = r.get_json()
        self.assertEqual((d['atualizados'], d['resultados'][0]['status'], d['resultados'][0]['planilha']), (1, 'OK', 'ok'))
        self.assertEqual(fake.puts[0][1], {'price': a['preco_novo']}); self.assertEqual(fake.preco, a['preco_novo'])

    def test_aplicar_exige_confirmacao_e_limites(self):
        fake = FakeML(preco=70.0)
        with self.com(fake):
            base = {'conta': 'ML19', 'parametros': {}, 'itens': [{'mlb': 'MLB123456789', 'custo': 30, 'meta': 25, 'preco_atual_visto': 70, 'preco_novo_visto': 100}]}
            self.assertEqual(self.c.post('/api/vendas/preco-aplicar', json=base).status_code, 400)                           # sem confirmação
            self.assertEqual(self.c.post('/api/vendas/preco-aplicar', json=dict(base, confirmacao='sim')).status_code, 400)
            muitos = [dict(base['itens'][0], mlb='MLB12345678%d' % i) for i in range(6)]
            self.assertEqual(self.c.post('/api/vendas/preco-aplicar', json=dict(base, itens=muitos, confirmacao=precos.CONFIRMACAO)).status_code, 400)
            repetido = [base['itens'][0], base['itens'][0]]
            self.assertEqual(self.c.post('/api/vendas/preco-aplicar', json=dict(base, itens=repetido, confirmacao=precos.CONFIRMACAO)).status_code, 400)
        self.assertEqual(fake.puts, [])

    def test_nao_aplica_se_algo_mudou_desde_que_a_pessoa_viu(self):
        fake = FakeML(preco=70.0)
        _, r = self.aplicar(fake, visto_atual=55.0)                                      # preço do anúncio mudou
        self.assertEqual(r.get_json()['resultados'][0]['status'], 'PULADO'); self.assertIn('mudou', r.get_json()['resultados'][0]['mensagem'])
        _, r = self.aplicar(FakeML(preco=70.0), visto_novo=50.0)                         # custo/tarifa/frete mudaram
        self.assertIn('mudaram', r.get_json()['resultados'][0]['mensagem'])
        self.assertEqual(fake.puts, [])

    def test_aumento_acima_do_limite_precisa_de_liberacao(self):
        fake = FakeML(preco=20.0)
        _, r = self.aplicar(fake)
        self.assertIn('liberação', r.get_json()['resultados'][0]['mensagem']); self.assertEqual(fake.puts, [])
        precos._RECENTES.clear()
        _, r = self.aplicar(FakeML(preco=20.0), liberar_acima_limite=True)
        self.assertEqual(r.get_json()['resultados'][0]['status'], 'OK')

    def test_clique_duplo_e_recusa_do_ml(self):
        fake = FakeML(preco=70.0)
        a, r = self.aplicar(fake)
        self.assertEqual(r.get_json()['resultados'][0]['status'], 'OK')
        fake.preco = 70.0                                                                # mesmo anúncio logo em seguida
        with self.com(fake):
            corpo = {'conta': 'ML19', 'parametros': {}, 'confirmacao': precos.CONFIRMACAO, 'itens': [{'mlb': 'MLB123456789', 'custo': 30, 'meta': 25, 'preco_atual_visto': 70, 'preco_novo_visto': a['preco_novo']}]}
            self.assertIn('acabou de ser alterado', self.c.post('/api/vendas/preco-aplicar', json=corpo).get_json()['resultados'][0]['mensagem'])
        precos._RECENTES.clear()
        recusa = FakeML(preco=70.0); recusa.recusa = True
        _, r = self.aplicar(recusa)
        self.assertEqual(r.get_json()['resultados'][0]['status'], 'ERRO'); self.assertEqual(r.get_json()['atualizados'], 0)


if __name__ == '__main__':
    unittest.main()
