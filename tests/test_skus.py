import json
import os
import sys
import unittest
from unittest import mock

os.environ['EXP_SECRET_KEY'] = 'teste'
os.environ['EXP_LOGIN_USER'] = 'admin'
from werkzeug.security import generate_password_hash
os.environ['EXP_LOGIN_PASSWORD_HASH'] = generate_password_hash('senha-de-teste')
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import anuncios
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
                             'variations': [{'id': 11, 'seller_custom_field': 'GR_P', 'attributes': [], 'attribute_combinations': [{'name': 'Tamanho', 'value_name': 'P'}, {'name': 'Cor', 'value_name': 'Branco'}]}, {'id': 12, 'seller_custom_field': None, 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'GR_G'}]}, {'id': 13, 'seller_custom_field': 'ZZ', 'attributes': []}]},
            'MLB100000005': {'id': 'MLB100000005', 'seller_id': 99, 'title': 'Encerrado', 'status': 'closed', 'seller_custom_field': 'GR_25kg', 'attributes': []},
            'MLB100000006': {'id': 'MLB100000006', 'seller_id': 7, 'title': 'De outra conta', 'status': 'active', 'seller_custom_field': 'GR_25kg', 'attributes': []},
        }
        self.puts, self.recusa, self.ignora = [], False, False

    def ml(self, tk, caminho, params=None):
        if caminho.endswith('/items/search'):
            if params.get('scroll_id') == 'FIM':
                return {'results': [], 'scroll_id': 'FIM', 'paging': {'total': len(self.itens)}}
            ids = [i for i, x in self.itens.items() if not params.get('status') or x['status'] == params['status']]
            if params.get('limit') == 1:
                ids = ids[:1]
            return {'results': ids, 'scroll_id': 'FIM', 'paging': {'total': len([1 for x in self.itens.values() if not params.get('status') or x['status'] == params['status']])}}
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
        if 'status' in json:
            it['status'] = json['status']
        if 'seller_custom_field' in json:
            it['seller_custom_field'] = json['seller_custom_field']
        if 'attributes' in json:
            it['attributes'] = json['attributes']
        if 'variations' in json:                                   # como diz a documentação do ML: a variação que não vai no PUT é apagada
            enviados = {v['id'] for v in json['variations']}
            it['variations'] = [x for x in it['variations'] if x['id'] in enviados]
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


class TestVarreduraRapida(unittest.TestCase):
    def rodar(self, ml):
        regra = skus.validar_regra('contem', '_25kg', 'NOVO', 'inteiro')
        achados, tok, passos = [], None, 0
        with mock.patch.object(fontes, '_ml', ml), mock.patch.object(fontes, 'token', lambda c: 't'), mock.patch.object(fontes, '_vendedor', lambda c, t: '99'):
            while True:
                d = skus.varrer('ML19', regra, tok); passos += 1
                achados += d['achados']; tok = d['scroll_id']
                if d['concluido'] or passos > 20:
                    return achados, d, passos

    def test_le_so_ativos_e_pausados_e_termina(self):
        fake = FakeML(); chamadas = []
        orig = fake.ml
        def ml(tk, caminho, params=None):
            chamadas.append((caminho, dict(params or {}))); return orig(tk, caminho, params)
        achados, d, passos = self.rodar(ml)
        self.assertEqual(sorted(a['mlb'] for a in achados), ['MLB100000001', 'MLB100000002'])
        lidos_ids = [i for c, p in chamadas if c == '/items' for i in p['ids'].split(',')]
        self.assertNotIn('MLB100000005', lidos_ids)                           # o encerrado não é lido
        self.assertEqual({p.get('status') for c, p in chamadas if c.endswith('/items/search')}, {'active', 'paused'})
        self.assertEqual(d['total'], 5)

    def test_se_o_ml_recusar_o_filtro_le_tudo_como_antes(self):
        fake = FakeML(); orig = fake.ml
        def ml(tk, caminho, params=None):
            if caminho.endswith('/items/search') and (params or {}).get('status'):
                raise RuntimeError('HTTP 400')
            return orig(tk, caminho, params)
        achados, d, _ = self.rodar(ml)
        self.assertEqual(sorted(a['mlb'] for a in achados), ['MLB100000001', 'MLB100000002'])
        self.assertTrue(d['concluido'])

    def test_varias_paginas_num_passo_sem_perder_nem_repetir(self):
        itens = {'MLB%d' % (200000000 + i): {'id': 'MLB%d' % (200000000 + i), 'seller_id': 99, 'title': 't', 'status': 'active', 'seller_custom_field': 'GR_25kg', 'attributes': []} for i in range(350)}
        ids = list(itens)
        def ml(tk, caminho, params=None):
            if caminho.endswith('/items/search'):
                if params.get('limit') == 1:
                    return {'results': ids[:1], 'paging': {'total': 350 if params['status'] == 'active' else 0}}
                if params['status'] == 'paused':
                    return {'results': [], 'scroll_id': 'S', 'paging': {'total': 0}}
                pos = int(params.get('scroll_id') or 0)
                return {'results': ids[pos:pos + 100], 'scroll_id': str(pos + 100), 'paging': {'total': 350}}
            return [{'code': 200, 'body': itens[i]} for i in params['ids'].split(',')]
        achados, d, passos = self.rodar(ml)
        self.assertEqual(len(achados), 350); self.assertEqual(len({a['mlb'] for a in achados}), 350)
        self.assertEqual(passos, 1)                                           # tudo num passo só


class TestFatias(unittest.TestCase):
    def test_fatias_por_tipo_andam_juntas_sem_perder_nem_repetir(self):
        itens = {}
        for i in range(250):
            m = 'MLB%d' % (300000000 + i)
            itens[m] = {'id': m, 'seller_id': 99, 'title': 't', 'status': 'active', 'listing_type_id': 'gold_pro' if i % 5 == 0 else 'gold_special', 'seller_custom_field': 'GR_25kg', 'attributes': []}
        vistos = []
        def ml(tk, caminho, params=None):
            if caminho.endswith('/items/search'):
                certos = [m for m, x in itens.items() if params.get('status') == x['status'] and (not params.get('listing_type_id') or params['listing_type_id'] == x['listing_type_id'])]
                if params.get('limit') == 1:
                    return {'results': certos[:1], 'paging': {'total': len(certos)}}
                pos = int(params.get('scroll_id') or 0)
                return {'results': certos[pos:pos + 100], 'scroll_id': str(pos + 100), 'paging': {'total': len(certos)}}
            vistos.extend(params['ids'].split(','))
            return [{'code': 200, 'body': itens[i]} for i in params['ids'].split(',')]
        regra = skus.validar_regra('contem', '_25kg', 'NOVO', 'inteiro')
        with mock.patch.object(fontes, '_ml', ml), mock.patch.object(fontes, 'token', lambda c: 't'), mock.patch.object(fontes, '_vendedor', lambda c, t: '99'):
            d = skus.varrer('ML19', regra)
            tok = json.loads(d['scroll_id'])
            fim = skus.varrer('ML19', regra, d['scroll_id'])
        self.assertEqual(len(tok['p']), 2)                                     # Clássico e Premium viraram duas fatias
        self.assertEqual(len(d['achados']) + len(fim['achados']), 250)
        self.assertEqual(len(vistos), 250); self.assertEqual(len(set(vistos)), 250)
        self.assertTrue(fim['concluido']); self.assertEqual(d['total'], 250)


class TestVarredura(unittest.TestCase):
    def test_varre_e_mostra_sem_alterar(self):
        fake = FakeML()
        regra = skus.validar_regra('contem', '_25kg', 'NOVO', 'inteiro')
        with mock.patch.object(fontes, '_ml', fake.ml), mock.patch.object(fontes, 'token', lambda c: 't'), mock.patch.object(fontes, '_vendedor', lambda c, t: '99'):
            d = skus.varrer('ML19', regra)
            fim = skus.varrer('ML19', regra, d['scroll_id'])
        mlbs = sorted(a['mlb'] for a in d['achados'])
        self.assertEqual(mlbs, ['MLB100000001', 'MLB100000002'])               # a de outra conta, as sem o trecho e a encerrada ficam de fora (só entra o que dá para alterar)
        self.assertEqual(d['lidos'], 4)                                         # encerrado nem é lido (a busca filtra por status)
        self.assertTrue(fim['concluido'])
        self.assertEqual(fake.puts, [])

    def test_variacoes_uma_linha_por_local(self):
        fake = FakeML()
        regra = skus.validar_regra('comeca', 'GR_', 'NV_', 'trecho')
        with mock.patch.object(fontes, '_ml', fake.ml), mock.patch.object(fontes, 'token', lambda c: 't'), mock.patch.object(fontes, '_vendedor', lambda c, t: '99'):
            d = skus.varrer('ML19', regra)
        var = [(a['variacao'], a['sku_atual'], a['sku_novo']) for a in d['achados'] if a['mlb'] == 'MLB100000004']
        self.assertEqual(sorted(var), [(11, 'GR_P', 'NV_P'), (12, 'GR_G', 'NV_G')])
        nomes = {a['variacao']: a['variacao_nome'] for a in d['achados'] if a['mlb'] == 'MLB100000004'}
        self.assertEqual(nomes, {11: 'Tamanho: P / Cor: Branco', 12: ''})                        # ML sem nome para a 12: fica vazio, nunca inventa
        self.assertEqual(skus.nome_variacao(fake.itens['MLB100000001'], None), '')

    def test_novo_vazio_ou_enorme_e_bloqueado_na_previa(self):
        fake = FakeML()
        regra = ('igual', 'GR_25kg', 'x' * 101, 'inteiro')
        a = skus.achados(fake.itens['MLB100000001'], regra)
        self.assertFalse(a[0]['permitido'])


class TestSemSku(unittest.TestCase):
    def setUp(self):
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)
        skus._RECENTES.clear()
        self.fake = FakeML()
        novos = {
            'MLB200000001': {'id': 'MLB200000001', 'seller_id': 99, 'title': 'Granito Grafiato 5kg', 'status': 'active', 'seller_custom_field': None, 'attributes': []},
            'MLB200000002': {'id': 'MLB200000002', 'seller_id': 99, 'title': 'Manta Asfaltica 10m', 'status': 'active', 'seller_custom_field': '', 'attributes': []},
            'MLB200000003': {'id': 'MLB200000003', 'seller_id': 99, 'title': '5kg Granito Rustico', 'status': 'paused', 'seller_custom_field': None, 'attributes': []},
            'MLB200000004': {'id': 'MLB200000004', 'seller_id': 99, 'title': 'Granito com variacoes', 'status': 'active', 'seller_custom_field': None, 'attributes': [],
                             'variations': [{'id': 21, 'seller_custom_field': 'JA_TEM', 'attributes': []}, {'id': 22, 'seller_custom_field': None, 'attributes': [], 'attribute_combinations': [{'name': 'Cor', 'value_name': 'Azul'}]}]},
            'MLB200000005': {'id': 'MLB200000005', 'seller_id': 99, 'title': 'Granito fechado', 'status': 'closed', 'seller_custom_field': None, 'attributes': []},
        }
        self.fake.itens = dict(novos, **{k: v for k, v in self.fake.itens.items() if k in ('MLB100000001',)})      # um com SKU para provar que não entra

    def com(self):
        from contextlib import ExitStack
        pilha = ExitStack()
        for p in self.fake.patches():
            pilha.enter_context(p)
        return pilha

    def buscar(self, modo, termo, novo='NOVO'):
        with self.com():
            return self.c.post('/api/skus/buscar', json={'conta': 'ML19', 'modo': modo, 'termo': termo, 'novo': novo}).get_json()

    def test_acha_so_os_sem_sku_pelo_titulo_contem_e_comeca(self):
        d = self.buscar('sem_contem', 'granito')
        achados = sorted((a['mlb'], a['variacao']) for a in d['achados'])
        self.assertEqual(achados, [('MLB200000001', None), ('MLB200000003', None), ('MLB200000004', 22)])     # variação com SKU, anúncio com SKU e encerrado ficam de fora
        d = self.buscar('sem_comeca', 'granito')
        self.assertEqual(sorted(a['mlb'] for a in d['achados']), ['MLB200000001', 'MLB200000004'])
        self.assertEqual(self.fake.puts, [])

    def test_ver_todos_sem_sku_lista_e_libera_o_preenchimento(self):
        d = self.buscar('sem_todos', '', '')
        self.assertTrue(d['ok'] and len(d['achados']) == 4)
        livres = {a['mlb']: a['permitido'] for a in d['achados']}
        self.assertEqual(livres['MLB200000001'], True)
        self.assertNotIn('MLB200000005', livres)                                                  # encerrado: nem aparece

    def aplicar_individual(self, itens):
        with self.com():
            return self.c.post('/api/skus/aplicar', json={'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'sem_todos', 'termo': '', 'novo': '', 'itens': itens}).get_json()

    def test_preenche_cada_anuncio_com_o_seu_proprio_sku_e_confere(self):
        d = self.aplicar_individual([{'mlb': 'MLB200000001', 'variacao': None, 'sku_visto': '', 'sku_novo': 'GR_5KG'},
                                     {'mlb': 'MLB200000002', 'variacao': None, 'sku_visto': '', 'sku_novo': 'MANTA_10M'},
                                     {'mlb': 'MLB200000004', 'variacao': 22, 'sku_visto': '', 'sku_novo': 'GR_VAR'}])
        self.assertEqual([r['status'] for r in d['resultados']], ['OK', 'OK', 'OK'])
        self.assertEqual((self.fake.itens['MLB200000001']['seller_custom_field'], self.fake.itens['MLB200000002']['seller_custom_field']), ('GR_5KG', 'MANTA_10M'))
        v = {x['id']: x for x in self.fake.itens['MLB200000004']['variations']}
        self.assertEqual((v[21]['seller_custom_field'], v[22]['seller_custom_field']), ('JA_TEM', 'GR_VAR'))

    def test_preenchimento_exige_sku_em_cada_anuncio_e_nao_mexe_em_quem_ja_tem(self):
        for item in ({'mlb': 'MLB200000001', 'variacao': None, 'sku_visto': ''}, {'mlb': 'MLB200000001', 'variacao': None, 'sku_visto': '', 'sku_novo': '  '},
                     {'mlb': 'MLB200000001', 'variacao': None, 'sku_visto': '', 'sku_novo': 'x' * 101}):
            with self.com():
                r = self.c.post('/api/skus/aplicar', json={'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'sem_todos', 'itens': [item]})
            self.assertEqual(r.status_code, 400)
        self.fake.itens['MLB200000001']['seller_custom_field'] = 'ALGUEM_PREENCHEU'
        d = self.aplicar_individual([{'mlb': 'MLB200000001', 'variacao': None, 'sku_visto': '', 'sku_novo': 'NOVO'}])
        self.assertEqual(d['resultados'][0]['status'], 'PULADO')
        self.assertEqual(self.fake.itens['MLB200000001']['seller_custom_field'], 'ALGUEM_PREENCHEU')
        self.assertEqual(self.fake.puts, [])

    def test_baixa_a_lista_em_excel(self):
        from io import BytesIO
        from openpyxl import load_workbook
        itens = [{'conta': 'ML19', 'mlb': 'MLB200000001', 'titulo': '=SOMA(1)', 'variacao': None, 'status': 'Ativo', 'sku_atual': '', 'sku_novo': 'GR_5KG', 'link': 'https://x'},
                 {'conta': 'ML44', 'mlb': 'MLB200000004', 'titulo': 'Granito', 'variacao': 22, 'variacao_nome': 'Cor: Azul', 'status': 'Ativo', 'sku_atual': '', 'sku_novo': '', 'link': ''}]
        r = self.c.post('/api/skus/exportar', json={'itens': itens, 'busca': 'Todos sem SKU'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('spreadsheetml', r.mimetype)
        ws = load_workbook(BytesIO(r.data))['Anúncios']
        linhas = [[c.value for c in l] for l in ws.iter_rows()]
        self.assertEqual(linhas[0][:3], ['Conta', 'MLB', 'Título do anúncio'])
        self.assertEqual((linhas[1][1], linhas[1][6], linhas[1][7], linhas[2][3]), ('MLB200000001', '(sem SKU)', 'GR_5KG', '22'))
        self.assertEqual((linhas[0][4], linhas[2][4]), ('Nome da variação', 'Cor: Azul'))
        self.assertTrue(linhas[1][2].startswith("'"))                                   # nada vira fórmula no Excel
        self.assertEqual(self.fake.puts, [])

    def test_baixar_exige_login_e_dados_validos(self):
        self.assertEqual(app_mod.app.test_client().post('/api/skus/exportar').status_code, 401)
        for corpo in ({'itens': []}, {'itens': [{'conta': 'XX', 'mlb': 'MLB200000001'}]}, {'itens': [{'conta': 'ML19', 'mlb': 'abc'}]}):
            self.assertEqual(self.c.post('/api/skus/exportar', json=corpo).status_code, 400)

    def test_exige_palavra_mas_nao_o_sku_novo_na_busca(self):
        with self.com():
            for corpo in ({'modo': 'sem_contem', 'termo': '', 'novo': 'X'}, {'modo': 'sem_contem', 'termo': 'g', 'novo': 'X'}, {'modo': 'sem_exato', 'termo': ''}):
                self.assertEqual(self.c.post('/api/skus/buscar', json=dict({'conta': 'ML19'}, **corpo)).status_code, 400)
            self.assertEqual(self.c.post('/api/skus/buscar', json={'conta': 'ML19', 'modo': 'sem_contem', 'termo': 'granito', 'novo': ''}).status_code, 200)

    def test_sem_sku_busca_por_titulo_grava_o_sku_de_cada_linha(self):
        d = self.buscar('sem_exato', 'GRANITO  grafiato 5KG', '')
        self.assertEqual([a['mlb'] for a in d['achados']], ['MLB200000001'])
        d = self.aplicar([{'mlb': 'MLB200000001', 'variacao': None, 'sku_visto': '', 'sku_novo': 'DA_LINHA'}], novo='')
        self.assertEqual(d['resultados'][0]['status'], 'OK')
        self.assertEqual(self.fake.itens['MLB200000001']['seller_custom_field'], 'DA_LINHA')
        with self.com():
            r = self.c.post('/api/skus/aplicar', json={'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'sem_contem', 'termo': 'manta', 'novo': '', 'itens': [{'mlb': 'MLB200000002', 'variacao': None, 'sku_visto': ''}]})
        self.assertEqual(r.status_code, 400)                                            # sem SKU na linha nem na regra: nada é gravado

    def aplicar(self, itens, termo='granito', novo='GR_5KG', modo='sem_contem'):
        with self.com():
            return self.c.post('/api/skus/aplicar', json={'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': modo, 'termo': termo, 'novo': novo, 'escopo': 'inteiro', 'itens': itens}).get_json()

    def test_coloca_sku_em_anuncio_sem_sku_e_confere(self):
        d = self.aplicar([{'mlb': 'MLB200000001', 'variacao': None, 'sku_visto': ''}])
        r = d['resultados'][0]
        self.assertEqual((r['status'], d['trocados']), ('OK', 1))
        self.assertEqual(self.fake.puts[0][1], {'seller_custom_field': 'GR_5KG', 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'GR_5KG'}]})
        self.assertEqual(self.fake.itens['MLB200000001']['seller_custom_field'], 'GR_5KG')

    def test_coloca_sku_so_na_variacao_sem_sku(self):
        d = self.aplicar([{'mlb': 'MLB200000004', 'variacao': 22, 'sku_visto': ''}])
        self.assertEqual(d['resultados'][0]['status'], 'OK')
        v = {x['id']: x for x in self.fake.itens['MLB200000004']['variations']}
        self.assertEqual((v[21]['seller_custom_field'], v[22]['seller_custom_field']), ('JA_TEM', 'GR_5KG'))

    def test_se_o_ml_recusar_o_atributo_tenta_so_o_campo_de_sku(self):
        original = self.fake.put
        def put(url, json=None, **kw):
            if json and 'attributes' in json:
                self.fake.puts.append((url, json))
                return mock.Mock(ok=False, status_code=400, text='atributo recusado')
            return original(url, json=json, **kw)
        self.fake.put = put
        d = self.aplicar([{'mlb': 'MLB200000001', 'variacao': None, 'sku_visto': ''}])
        self.assertEqual(d['resultados'][0]['status'], 'OK')
        self.assertEqual(self.fake.puts[-1][1], {'seller_custom_field': 'GR_5KG'})

    def test_anuncio_que_ganhou_sku_ou_mudou_de_titulo_nao_e_alterado(self):
        self.fake.itens['MLB200000001']['seller_custom_field'] = 'ALGUEM_PREENCHEU'
        self.fake.itens['MLB200000002']['title'] = 'Manta sem a palavra'
        for mlb in ('MLB200000001', 'MLB200000002'):
            d = self.aplicar([{'mlb': mlb, 'variacao': None, 'sku_visto': ''}], termo='granito' if mlb.endswith('1') else 'manta asf')
            self.assertEqual(d['resultados'][0]['status'], 'PULADO', mlb)
        self.assertEqual(self.fake.puts, [])

    def test_sku_visto_vazio_continua_proibido_nos_modos_com_sku(self):
        with self.com():
            r = self.c.post('/api/skus/aplicar', json={'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'igual', 'termo': 'GR_25kg', 'novo': 'X', 'itens': [{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': ''}]})
        self.assertEqual(r.status_code, 400)


class TestBuscaPorTitulo(unittest.TestCase):
    def setUp(self):
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)
        skus._RECENTES.clear()
        self.fake = FakeML()
        self.fake.itens = {
            'MLB300000001': {'id': 'MLB300000001', 'seller_id': 99, 'title': 'Manta Asfáltica Aluminizada 10m', 'status': 'active', 'seller_custom_field': 'MANTA_10', 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'MANTA_10'}]},
            'MLB300000002': {'id': 'MLB300000002', 'seller_id': 99, 'title': 'Aluminizada manta asfaltica 5m', 'status': 'paused', 'seller_custom_field': None, 'attributes': []},
            'MLB300000003': {'id': 'MLB300000003', 'seller_id': 99, 'title': 'Manta com variações', 'status': 'active', 'seller_custom_field': None, 'attributes': [],
                             'variations': [{'id': 31, 'seller_custom_field': 'VAR_A', 'attributes': []}, {'id': 32, 'seller_custom_field': None, 'attributes': []}, {'id': 33, 'seller_custom_field': 'VAR_C', 'attributes': []}]},
            'MLB300000004': {'id': 'MLB300000004', 'seller_id': 99, 'title': 'Manta encerrada', 'status': 'closed', 'seller_custom_field': 'X', 'attributes': []},
        }

    def com(self):
        from contextlib import ExitStack
        pilha = ExitStack()
        for p in self.fake.patches():
            pilha.enter_context(p)
        return pilha

    def buscar(self, modo, termo):
        with self.com():
            return self.c.post('/api/skus/buscar', json={'conta': 'ML19', 'modo': modo, 'termo': termo}).get_json()

    def aplicar(self, itens, modo='tit_contem', termo='manta'):
        with self.com():
            return self.c.post('/api/skus/aplicar', json={'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': modo, 'termo': termo, 'itens': itens}).get_json()

    def test_regra_do_titulo(self):
        c = skus.titulo_casa
        t = 'Manta Asfáltica  Aluminizada 10m'
        self.assertTrue(c(t, 'tit_contem', 'aluminizada ASFALTICA'))                    # todas as palavras, em qualquer ordem, sem ligar para acento
        self.assertFalse(c(t, 'tit_contem', 'aluminizada granito'))
        self.assertTrue(c(t, 'tit_comeca', 'manta asf'))
        self.assertFalse(c(t, 'tit_comeca', 'asfaltica'))
        self.assertTrue(c(t, 'tit_exato', 'manta asfaltica aluminizada 10M'))
        self.assertFalse(c(t, 'tit_exato', 'manta asfaltica aluminizada'))

    def test_busca_traz_com_e_sem_sku_e_cada_variacao(self):
        d = self.buscar('tit_contem', 'manta')
        linhas = sorted((a['mlb'], str(a['variacao']), a['sku_atual']) for a in d['achados'])
        self.assertEqual(linhas, [('MLB300000001', 'None', 'MANTA_10'), ('MLB300000002', 'None', ''), ('MLB300000003', '31', 'VAR_A'), ('MLB300000003', '32', ''), ('MLB300000003', '33', 'VAR_C')])
        self.assertEqual(self.fake.puts, [])
        self.assertEqual([a['mlb'] for a in self.buscar('tit_comeca', 'aluminizada')['achados']], ['MLB300000002'])
        self.assertEqual([a['mlb'] for a in self.buscar('tit_exato', 'manta asfaltica aluminizada 10m')['achados']], ['MLB300000001'])

    def test_grava_sku_digitado_em_quem_tem_e_em_quem_nao_tem(self):
        d = self.aplicar([{'mlb': 'MLB300000001', 'variacao': None, 'sku_visto': 'MANTA_10', 'sku_novo': 'MANTA_NOVA'}, {'mlb': 'MLB300000002', 'variacao': None, 'sku_visto': '', 'sku_novo': 'MANTA_5'}])
        self.assertEqual([r['status'] for r in d['resultados']], ['OK', 'OK'])
        self.assertEqual(self.fake.itens['MLB300000001']['seller_custom_field'], 'MANTA_NOVA')
        self.assertEqual(self.fake.itens['MLB300000001']['attributes'][0]['value_name'], 'MANTA_NOVA')
        self.assertEqual(self.fake.itens['MLB300000002']['seller_custom_field'], 'MANTA_5')

    def test_variacao_marcada_muda_e_as_outras_continuam_no_anuncio(self):
        d = self.aplicar([{'mlb': 'MLB300000003', 'variacao': 32, 'sku_visto': '', 'sku_novo': 'VAR_B'}])
        self.assertEqual(d['resultados'][0]['status'], 'OK')
        self.assertEqual([(v['id'], v['seller_custom_field']) for v in self.fake.itens['MLB300000003']['variations']], [(31, 'VAR_A'), (32, 'VAR_B'), (33, 'VAR_C')])

    def test_se_o_ml_apagar_variacao_nunca_diz_ok(self):
        original = self.fake.put
        def put(url, json=None, **kw):
            r = original(url, json=json, **kw)
            self.fake.itens[url.split('/')[-1]]['variations'].pop()
            return r
        self.fake.put = put
        r = self.aplicar([{'mlb': 'MLB300000003', 'variacao': 32, 'sku_visto': '', 'sku_novo': 'VAR_B'}])['resultados'][0]
        self.assertEqual(r['status'], 'NAO_CONFIRMADO')
        self.assertIn('33', r['mensagem'])

    def test_nao_grava_se_mudou_desde_que_viu(self):
        for visto, mlb in (('', 'MLB300000001'), ('OUTRO', 'MLB300000001'), ('VAR_A', 'MLB300000002')):      # visto vazio não passa em quem já tem SKU
            r = self.aplicar([{'mlb': mlb, 'variacao': None, 'sku_visto': visto, 'sku_novo': 'N'}])['resultados'][0]
            self.assertEqual(r['status'], 'PULADO', (visto, mlb))
        r = self.aplicar([{'mlb': 'MLB300000001', 'variacao': None, 'sku_visto': 'MANTA_10', 'sku_novo': 'N'}], termo='granito')['resultados'][0]
        self.assertEqual(r['status'], 'PULADO')
        r = self.aplicar([{'mlb': 'MLB300000001', 'variacao': None, 'sku_visto': 'MANTA_10', 'sku_novo': 'MANTA_10'}])['resultados'][0]
        self.assertEqual(r['status'], 'PULADO')
        self.assertEqual(self.fake.puts, [])

    def test_exige_sku_em_cada_linha_e_termo(self):
        with self.com():
            r = self.c.post('/api/skus/aplicar', json={'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'tit_contem', 'termo': 'manta', 'itens': [{'mlb': 'MLB300000001', 'variacao': None, 'sku_visto': 'MANTA_10'}]})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(self.c.post('/api/skus/buscar', json={'conta': 'ML19', 'modo': 'tit_contem', 'termo': 'm'}).status_code, 400)
        self.assertEqual(self.fake.puts, [])


class TestPausarPorSku(unittest.TestCase):
    def setUp(self):
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)
        anuncios._RECENTES.clear()
        self.fake = FakeML()
        self.fake.itens['MLB100000007'] = {'id': 'MLB100000007', 'seller_id': 99, 'title': 'Pausado', 'status': 'paused', 'seller_custom_field': 'GR_25KG', 'attributes': []}

    def com(self):
        from contextlib import ExitStack
        pilha = ExitStack()
        for p in self.fake.patches():
            pilha.enter_context(p)
        return pilha

    def buscar(self, termo):
        with self.com():
            return self.c.post('/api/skus/buscar', json={'conta': 'ML19', 'modo': 'pausar_sku', 'termo': termo})

    def pausar(self, itens):
        with self.com():
            return self.c.post('/api/vendas/pausar', json={'conta': 'ML19', 'confirmacao': 'PAUSAR_ANUNCIOS', 'itens': itens}).get_json()['resultados']

    def test_acha_so_ativos_com_o_sku_exato_e_nao_altera(self):
        d = self.buscar('gr_25KG').get_json()
        self.assertEqual([a['mlb'] for a in d['achados']], ['MLB100000001'])            # pausado, encerrado e de outra conta ficam de fora; "GR_25kg_x" não casaria
        self.assertEqual(self.fake.puts, [])
        self.assertEqual(self.buscar('').status_code, 400)
        self.assertEqual(self.buscar('GR_25').get_json()['achados'], [])

    def test_avisa_que_a_pausa_vale_para_as_outras_variacoes(self):
        a = self.buscar('GR_P').get_json()['achados'][0]
        self.assertEqual((a['mlb'], a['variacao_nome']), ('MLB100000004', 'Tamanho: P / Cor: Branco'))
        self.assertIn('2 de 3 variações', a['aviso'])

    def test_pausa_so_se_ainda_tem_o_sku(self):
        r = self.pausar([{'mlb': 'MLB100000001', 'sku_visto': 'GR_25KG'}, {'mlb': 'MLB100000003', 'sku_visto': 'GR_25KG'}])
        por = {x['mlb']: x['status'] for x in r}
        self.assertEqual(por, {'MLB100000001': 'OK', 'MLB100000003': 'PULADO'})
        self.assertEqual(self.fake.itens['MLB100000001']['status'], 'paused')
        self.assertEqual(self.fake.itens['MLB100000003']['status'], 'active')

    def test_modo_de_pausar_nao_troca_sku(self):
        with self.com():
            r = self.c.post('/api/skus/aplicar', json={'conta': 'ML19', 'confirmacao': 'TROCAR_SKU', 'modo': 'pausar_sku', 'termo': 'GR_25kg', 'itens': [{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg', 'sku_novo': 'X'}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.fake.puts, [])


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

    def test_se_o_ml_recusar_os_dois_campos_juntos_tenta_so_o_campo_de_sku(self):
        """SKU nos dois campos: o ML às vezes responde 400 'attributes.duplicated' quando os dois vão juntos."""
        fake = FakeML()
        original = fake.put
        def put(url, json=None, **kw):
            if json and 'attributes' in json and 'seller_custom_field' in json:
                fake.puts.append((url, json))
                return mock.Mock(ok=False, status_code=400, text='{"cause":[{"code":"attributes.duplicated"}]}')
            r = original(url, json=json, **kw)
            if json and 'seller_custom_field' in json:             # o ML copia o campo de SKU para o atributo SELLER_SKU
                fake.itens[url.split('/')[-1]]['attributes'] = [{'id': 'SELLER_SKU', 'value_name': json['seller_custom_field']}]
            return r
        fake.put = put
        with self.com(fake):
            d = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}])).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'OK', d)
        self.assertEqual(fake.puts[-1][1], {'seller_custom_field': 'GR_25KG'})

    def test_recusa_que_continua_mostra_motivo_em_portugues(self):
        fake = FakeML()
        fake.recusa = True
        fake.put_texto = None
        with self.com(fake):
            d = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}])).get_json()
        self.assertEqual(d['resultados'][0]['status'], 'ERRO')
        self.assertEqual(len(fake.puts), 2)                          # tentou duas formas
        m = skus._motivo_recusa('{"cause":[{"code":"attributes.duplicated","message":"Attributes with names: [SELLER_SKU] are duplicated"}]}')
        self.assertIn('[SELLER_SKU] are duplicated', m)                 # mensagem inteira, não cortada
        self.assertIn('atributo repetido', m)

    def test_recusa_duplicated_diz_o_que_esta_repetido_no_anuncio(self):
        fake = FakeML()
        fake.itens['MLB100000001']['attributes'] = [{'id': 'SELLER_SKU', 'value_name': 'GR_25kg'}, {'id': 'SELLER_SKU', 'value_name': 'GR_25kg'}]
        fake.itens['MLB100000001']['user_product_id'] = 'MLBU1'
        original = fake.put
        def put(url, json=None, **kw):
            fake.puts.append((url, json))
            return mock.Mock(ok=False, status_code=400, text='{"cause":[{"code":"attributes.duplicated","message":"Attributes with names: [SELLER_SKU]"}]}')
        fake.put = put
        with self.com(fake):
            d = self.c.post('/api/skus/aplicar', json=self.corpo([{'mlb': 'MLB100000001', 'variacao': None, 'sku_visto': 'GR_25kg'}])).get_json()
        msg = d['resultados'][0]['mensagem']
        self.assertEqual(d['resultados'][0]['status'], 'ERRO')
        self.assertIn('SELLER_SKU (2x)', msg)
        self.assertIn('produto do usuário', msg)

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
        self.assertEqual(fake.puts[0][1], {'variations': [{'id': 11}, {'id': 12, 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'NV_G'}]}, {'id': 13}]})   # as outras vão só com o id, senão o ML as apaga
        self.assertEqual([x['id'] for x in fake.itens['MLB100000004']['variations']], [11, 12, 13])

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
            muitos = [{'mlb': 'MLB10000000%d' % i, 'variacao': None, 'sku_visto': 'x'} for i in range(1, 10)] + [{'mlb': 'MLB1000000%d' % i, 'variacao': None, 'sku_visto': 'x'} for i in (10, 11)]
            self.assertEqual(self.c.post('/api/skus/aplicar', json=self.corpo(muitos)).status_code, 400)
            self.assertEqual(self.c.post('/api/skus/aplicar', json=self.corpo(item + item)).status_code, 400)


if __name__ == '__main__':
    unittest.main()


class DiagnosticoTest(unittest.TestCase):
    def test_mostra_sku_fotos_e_diferenca_da_varredura(self):
        completo = {'id': 'MLB1000000001', 'seller_id': 7, 'status': 'active', 'category_id': 'MLB9', 'pictures': [{}] * 13,
                    'variations': [{'id': 1, 'picture_ids': ['a', 'b'], 'attribute_combinations': [{'name': 'Cor', 'value_name': 'Areia'}],
                                    'attributes': [{'id': 'SELLER_SKU', 'value_name': 'X1'}]}]}
        varredura_item = dict(completo, variations=[{'id': 1, 'attribute_combinations': []}])   # a busca não trouxe o SKU

        def ml(tk, caminho, params=None):
            if caminho == '/items/MLB1000000001': return completo
            if caminho == '/items': return [{'code': 200, 'body': varredura_item}]
            if caminho == '/categories/MLB9': return {'max_pictures_per_item': 12, 'max_pictures_per_item_var': 10}
        with mock.patch.object(fontes, 'token', return_value='t'), mock.patch.object(fontes, '_vendedor', return_value=7), mock.patch.object(fontes, '_ml', ml):
            d = skus.diagnosticar('ML19', 'MLB1000000001')
        self.assertEqual(d['fotos_do_anuncio'], 13); self.assertEqual(d['limite_fotos_anuncio'], 12)
        self.assertEqual(d['variacoes'][0]['SELLER_SKU'], 'X1')
        self.assertEqual(d['sem_sku_lendo_o_anuncio_completo'], [])
        self.assertEqual(d['sem_sku_como_a_varredura_enxerga'], [1])
