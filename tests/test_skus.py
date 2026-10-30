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
import skus


class FakeML:
    """ML de mentira com anúncios simples e com variações; o SKU fica em seller_custom_field e/ou atributo SELLER_SKU."""
    def __init__(self):
        self.itens = {
            'MLB100000001': {'id': 'MLB100000001', 'seller_id': 99, 'title': 'Granito 25kg', 'status': 'active', 'seller_custom_field': 'GR_25kg', 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'GR_25kg'}]},
            'MLB100000002': {'id': 'MLB100000002', 'seller_id': 99, 'title': 'Granito 25kg pausado', 'status': 'paused', 'seller_custom_field': 'gr_25KG', 'attributes': []},
            'MLB100000003': {'id': 'MLB100000003', 'seller_id': 99, 'title': 'Outro', 'status': 'active', 'seller_custom_field': 'XX_1kg', 'attributes': []},
            'MLB100000004': {'id': 'MLB100000004', 'seller_id': 99, 'title': 'Com variações', 'status': 'active', 'seller_custom_field': None, 'attributes': [],
                             'variations': [{'id': 11, 'seller_custom_field': 'GR_P', 'attributes': []}, {'id': 12, 'seller_custom_field': None, 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'GR_G'}]}, {'id': 13, 'seller_custom_field': 'ZZ', 'attributes': []}]},
            'MLB100000005': {'id': 'MLB100000005', 'seller_id': 99, 'title': 'Encerrado', 'status': 'closed', 'seller_custom_field': 'GR_25kg', 'attributes': []},
            'MLB100000006': {'id': 'MLB100000006', 'seller_id': 7, 'title': 'De outra conta', 'status': 'active', 'seller_custom_field': 'GR_25kg', 'attributes': []},
        }
        self.puts, self.recusa, self.ignora = [], False, False

    def ml(self, tk, caminho, params=None):
        if caminho.endswith('/items/search'):
            if params.get('scroll_id') == 'FIM':
                return {'results': [], 'scroll_id': 'FIM', 'paging': {'total': len(self.itens)}}
            return {'results': list(self.itens), 'scroll_id': 'FIM', 'paging': {'total': len(self.itens)}}
        if caminho == '/items':
            return [{'code': 200, 'body': self.itens[i]} for i in params['ids'].split(',')]
        if caminho.startswith('/items/'):
            return self.itens[caminho.split('/')[-1]]
        raise RuntimeError('rota inesperada ' + caminho)

    def put(self, url, json=None, **kw):
        self.puts.append((url, json))
        r = mock.Mock(ok=not self.recusa, status_code=400 if self.recusa else 200, text='recusado')
        if self.recusa or self.ignora:
            return r
        it = self.itens[url.split('/')[-1]]
        if 'seller_custom_field' in json:
            it['seller_custom_field'] = json['seller_custom_field']
        if 'attributes' in json:
            it['attributes'] = json['attributes']
        for v in json.get('variations', []):
            alvo = next(x for x in it['variations'] if x['id'] == v['id'])
            if 'seller_custom_field' in v:
                alvo['seller_custom_field'] = v['seller_custom_field']
            if 'attributes' in v:
                alvo['attributes'] = v['attributes']
        return r

    def patches(self):
        return [mock.patch.object(fontes, '_ml', self.ml), mock.patch.object(fontes._SESS, 'put', self.put), mock.patch.object(fontes, 'token', lambda c: 't'),
                mock.patch.object(fontes, '_vendedor', lambda c, t: '99')]


class TestRegras(unittest.TestCase):
    def test_trocar_modos_e_escopos(self):
        t = skus.trocar
        self.assertEqual(t('GR_25kg', 'igual', 'gr_25KG', 'NOVO', 'inteiro'), 'NOVO')          # maiúsculas não importam
        self.assertIsNone(t('GR_25kg_x', 'igual', 'GR_25kg', 'NOVO', 'inteiro'))
        self.assertEqual(t('GR_25kg_x', 'contem', '_25KG', 'NOVO', 'inteiro'), 'NOVO')
        self.assertEqual(t('GR_25kg_x', 'contem', '_25KG', '_25Kg', 'trecho'), 'GR_25Kg_x')
        self.assertEqual(t('GR_25kg', 'comeca', 'gr_', 'OK_', 'trecho'), 'OK_25kg')
        self.assertEqual(t('GR_25kg', 'comeca', 'gr_', 'OK', 'inteiro'), 'OK')
        self.assertIsNone(t('XGR_25kg', 'comeca', 'GR', 'OK', 'inteiro'))
        self.assertIsNone(t('', 'igual', 'a', 'b', 'inteiro'))

    def test_validar_regra(self):
        self.assertEqual(skus.validar_regra('igual', ' A ', ' B ', 'trecho'), ('igual', 'A', 'B', 'inteiro'))
        for args in (('x', 'a', 'b', 'inteiro'), ('igual', '', 'b', 'inteiro'), ('igual', 'a', '', 'inteiro'), ('contem', 'a', 'b', 'inteiro'), ('igual', 'a', 'b' * 101, 'inteiro'), ('igual', 'a', 'b\nc', 'inteiro')):
            with self.assertRaises(skus.Bloqueio):
                skus.validar_regra(*args)


class TestVarredura(unittest.TestCase):
    def test_varre_e_mostra_sem_alterar(self):
        fake = FakeML()
        regra = skus.validar_regra('contem', '_25kg', 'NOVO', 'inteiro')
        with mock.patch.object(fontes, '_ml', fake.ml), mock.patch.object(fontes, 'token', lambda c: 't'), mock.patch.object(fontes, '_vendedor', lambda c, t: '99'):
            d = skus.varrer('ML19', regra)
            fim = skus.varrer('ML19', regra, d['scroll_id'])
        mlbs = sorted(a['mlb'] for a in d['achados'])
        self.assertEqual(mlbs, ['MLB100000001', 'MLB100000002', 'MLB100000005'])               # a de outra conta e as sem o trecho ficam de fora
        enc = next(a for a in d['achados'] if a['mlb'] == 'MLB100000005')
        self.assertFalse(enc['permitido'])
        self.assertEqual(d['lidos'], 5)
        self.assertTrue(fim['concluido'])
        self.assertEqual(fake.puts, [])

    def test_variacoes_uma_linha_por_local(self):
        fake = FakeML()
        regra = skus.validar_regra('comeca', 'GR_', 'NV_', 'trecho')
        with mock.patch.object(fontes, '_ml', fake.ml), mock.patch.object(fontes, 'token', lambda c: 't'), mock.patch.object(fontes, '_vendedor', lambda c, t: '99'):
            d = skus.varrer('ML19', regra)
        var = [(a['variacao'], a['sku_atual'], a['sku_novo']) for a in d['achados'] if a['mlb'] == 'MLB100000004']
        self.assertEqual(sorted(var), [(11, 'GR_P', 'NV_P'), (12, 'GR_G', 'NV_G')])

    def test_novo_vazio_ou_enorme_e_bloqueado_na_previa(self):
        fake = FakeML()
        regra = ('igual', 'GR_25kg', 'x' * 101, 'inteiro')
        a = skus.achados(fake.itens['MLB100000001'], regra)
        self.assertFalse(a[0]['permitido'])


class TestEndpoints(unittest.TestCase):
    def setUp(self):
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)
        skus._RECENTES.clear()

    def com(self, fake):
        from contextlib import ExitStack
        pilha = ExitStack()
        for p in fake.patches():
            pilha.enter_context(p)
        return pilha

    def corpo(self, itens, **kw):
        return dict({'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'igual', 'termo': 'GR_25kg', 'novo': 'GR_25KG', 'escopo': 'inteiro', 'itens': itens}, **kw)

    def test_exige_login_e_pagina_no_menu(self):
        c = app_mod.app.test_client()
        for rota in ('/api/skus/buscar', '/api/skus/aplicar'):
            self.assertEqual(c.post(rota).status_code, 401)
        html = self.c.get('/skus').get_data(as_text=True)
        self.assertIn('Trocar SKU', html)
        self.assertIn('skus.js', html)

    def test_buscar_e_somente_leitura(self):
        fake = FakeML()
        with self.com(fake):
            d = self.c.post('/api/skus/buscar', json={'conta': 'ML19', 'modo': 'igual', 'termo': 'GR_25kg', 'novo': 'N'}).get_json()
        self.assertTrue(d['ok'] and d['somente_leitura'])
        self.assertEqual(fake.puts, [])

    def test_aplicar_exige_confirmacao_e_conta(self):
        fake = FakeML()
        with self.com(fake):
            r = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}], confirmacao='nao'))
            self.assertEqual(r.status_code, 400)
            r = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}], conta='XX'))
            self.assertEqual(r.status_code, 400)
        self.assertEqual(fake.puts, [])

    def test_troca_anuncio_simples_nos_dois_campos_e_confere(self):
        fake = FakeML()
        with self.com(fake):
            d = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}])).get_json()
        r = d['resultados'][0]
        self.assertEqual((r['status'], d['trocados'], d['somente_leitura']), ('OK', 1, False))
        self.assertEqual(fake.puts[0][1], {'seller_custom_field': 'GR_25KG', 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'GR_25KG'}]})
        self.assertEqual(fake.itens['MLB100000001']['seller_custom_field'], 'GR_25KG')

    def test_troca_so_a_variacao_marcada(self):
        fake = FakeML()
        corpo = self.corpo([{'mlb': 'MLB100000004', 'variacao': 12, 'sku_visto': 'GR_G'}], modo='comeca', termo='GR_', novo='NV_', escopo='trecho')
        with self.com(fake):
            d = self.c.post('/api/skus/aplicar', json=corpo).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'OK')
        v = {x['id']: x for x in fake.itens['MLB100000004']['variations']}
        self.assertEqual(v[11]['seller_custom_field'], 'GR_P')                                   # não marcada: intacta
        self.assertEqual(v[12]['attributes'][0]['value_name'], 'NV_G')
        self.assertEqual(fake.puts[0][1], {'variations': [{'id': 12, 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'NV_G'}]}]})

    def test_sku_mudou_desde_que_viu_nao_altera(self):
        fake = FakeML()
        with self.com(fake):
            d = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000003', 'variacao': None, 'sku_visto': 'GR_25kg'}])).get_json()
        r = d['resultados'][0]
        self.assertEqual(r['status'], 'PULADO')
        self.assertIn('mudou', r['mensagem'])
        self.assertEqual(fake.puts, [])

    def test_bloqueios_encerrado_e_outra_conta(self):
        fake = FakeML()
        with self.com(fake):
            for mlb in ('MLB100000005', 'MLB100000006'):
                d = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': mlb, 'variacao': None, 'sku_visto': 'GR_25kg'}])).get_json()
                self.assertEqual(d['resultados'][0]['status'], 'PULADO')
        self.assertEqual(fake.puts, [])

    def test_recusa_do_ml_vira_erro(self):
        fake = FakeML(); fake.recusa = True
        with self.com(fake):
            d = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}])).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'ERRO')
        self.assertEqual(d['trocados'], 0)

    def test_ml_aceita_mas_nao_grava_nunca_diz_ok(self):
        fake = FakeML(); fake.ignora = True
        with self.com(fake):
            d = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}])).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'NAO_CONFIRMADO')
        self.assertEqual(d['trocados'], 0)

    def test_clique_duplo_e_limites(self):
        fake = FakeML()
        item = [{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}]
        with self.com(fake):
            self.assertEqual(self.c.post('/api/skus/aplicar', json=self.corpo(item)).get_json()['resultados'][0]['status'], 'OK')
            skus_visto = [{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25KG'}]
            r = self.c.post('/api/skus/aplicar', json=self.corpo(skus_visto, termo='GR_25KG', novo='OUTRO')).get_json()['resultados'][0]
            self.assertEqual(r['status'], 'PULADO')
            self.assertIn('acabou de ser alterado', r['mensagem'])
            muitos = [{'mlb': 'MLB10000000%d' % i, 'variacao': None, 'sku_visto': 'x'} for i in range(1, 7)]
            self.assertEqual(self.c.post('/api/skus/aplicar', json=self.corpo(muitos)).status_code, 400)
            self.assertEqual(self.c.post('/api/skus/aplicar', json=self.corpo(item + item)).status_code, 400)


if __name__ == '__main__':
    unittest.main()
