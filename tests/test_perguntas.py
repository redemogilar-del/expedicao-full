import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import requests

import test_skus          # define as variáveis EXP_* de teste
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app as app_mod
import fontes
import perguntas


def ha(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


class FakeML:
    def __init__(self):
        self.questoes = {
            '111': {'id': 111, 'seller_id': 99, 'item_id': 'MLB100000001', 'status': 'UNANSWERED', 'text': 'qual a gramatura?', 'date_created': ha(hours=17), 'from': {'id': 501}, 'answer': None},
            '222': {'id': 222, 'seller_id': 99, 'item_id': 'MLB100000002', 'status': 'ANSWERED', 'text': 'tem nota?', 'date_created': ha(days=20), 'from': {'id': 502}, 'answer': {'text': 'Sim.', 'status': 'ACTIVE', 'date_created': ha(days=19)}},
            '333': {'id': 333, 'seller_id': 7, 'item_id': 'MLB100000001', 'status': 'UNANSWERED', 'text': 'de outra conta', 'date_created': ha(hours=1), 'from': {'id': 503}, 'answer': None},
        }
        self.posts, self.recusa, self.queda, self.ignora, self.falha_itens, self.negado = [], False, False, False, False, False
        self.buscas = []

    def ml(self, tk, caminho, params=None):
        if caminho == '/questions/search':
            if self.negado:
                raise RuntimeError('403 Client Error: Forbidden')
            self.buscas.append(params)
            lista = sorted([q for q in self.questoes.values() if q['seller_id'] == 99 and (not params.get('status') or q['status'] == params['status'])], key=lambda q: q['date_created'], reverse=True)
            return {'total': len(lista), 'limit': params['limit'], 'questions': lista[params['offset']:params['offset'] + params['limit']]}
        if caminho.startswith('/questions/'):
            return self.questoes[caminho.split('/')[-1]]
        if caminho == '/items':
            if self.falha_itens:
                raise RuntimeError('HTTP 500')
            return [{'code': 200, 'body': {'id': 'MLB100000001', 'title': 'Manta Geotêxtil', 'status': 'active', 'price': 651.25, 'available_quantity': 999, 'seller_custom_field': 'GEO2', 'attributes': [], 'shipping': {'free_shipping': True}, 'permalink': 'https://produto.mercadolivre.com.br/x', 'thumbnail': 'http://http2.mlstatic.com/a.jpg'}},
                    {'code': 200, 'body': {'id': 'MLB100000002', 'title': 'Kit Dreno', 'price': 278.39, 'attributes': [], 'variations': [{'id': 1, 'seller_custom_field': 'KIT_A', 'attributes': []}, {'id': 2, 'seller_custom_field': 'KIT_B', 'attributes': []}], 'permalink': 'javascript:alert(1)', 'thumbnail': 'https://mal.example/a.jpg'}}]
        raise RuntimeError('rota inesperada ' + caminho)

    def get(self, url, **kw):
        if url.endswith('/questions/response_time'):
            return self.tempo
        if url.endswith('/users/501'):
            return mock.Mock(ok=True, json=lambda: {'nickname': 'RAFAELA'})
        return mock.Mock(ok=False, json=lambda: {})

    tempo = mock.Mock(status_code=200, raise_for_status=lambda: None, json=lambda: {
        'total': {'response_time': 1309}, 'weekdays_working_hours': {'response_time': 100, 'sales_percent_increase': 10},
        'weekdays_extra_hours': {'response_time': 45}, 'weekend': {'response_time': 200, 'sales_percent_increase': 5}})

    def post(self, url, json=None, **kw):
        self.posts.append((url, json))
        if self.queda:
            raise requests.ConnectionError('caiu')
        if self.recusa:
            return mock.Mock(ok=False, status_code=400, text='x', json=lambda: {'message': 'Resposta com dados de contato'})
        if not self.ignora:
            q = self.questoes[str(json['question_id'])]
            q['status'], q['answer'] = 'ANSWERED', {'text': json['text'], 'status': 'ACTIVE', 'date_created': ha(seconds=1)}
        return mock.Mock(ok=True, status_code=200, text='')


class Base(unittest.TestCase):
    def setUp(self):
        perguntas._RECENTES.clear()
        self.f = FakeML()
        for p in (mock.patch.object(fontes, '_ml', self.f.ml), mock.patch.object(fontes._SESS, 'post', self.f.post), mock.patch.object(fontes._SESS, 'get', self.f.get),
                  mock.patch.object(fontes, 'token', lambda c: 't'), mock.patch.object(fontes, '_vendedor', lambda c, t: '99')):
            p.start(); self.addCleanup(p.stop)
        app_mod.app.config['TESTING'] = True
        self.c = app_mod.app.test_client()
        self.assertEqual(self.c.post('/login', data={'usuario': 'admin', 'senha': 'senha-de-teste'}).status_code, 302)

    def post(self, rota, corpo):
        return self.c.post(rota, json=corpo)

    def responder(self, pergunta='111', texto='Gramatura de 150 g/m².', confirmacao=perguntas.CONFIRMACAO, conta='ML19'):
        return self.post('/api/perguntas/responder', {'conta': conta, 'pergunta': pergunta, 'texto': texto, 'confirmacao': confirmacao})


class TestListar(Base):
    def test_a_responder_com_produto(self):
        d = self.post('/api/perguntas/listar', {'conta': 'ML19'}).get_json()
        self.assertTrue(d['ok'])
        self.assertEqual([q['id'] for q in d['perguntas']], ['111'])
        q = d['perguntas'][0]
        self.assertEqual((q['comprador'], q['item']['sku'], q['item']['preco'], q['item']['frete_gratis']), ('RAFAELA', 'GEO2', 651.25, True))
        self.assertEqual(q['item']['thumb'], 'https://http2.mlstatic.com/a.jpg')
        self.assertEqual(self.f.buscas[0]['status'], 'UNANSWERED')
        self.assertEqual(self.f.buscas[0]['api_version'], 4)

    def test_respondidas_variacoes_e_endereco_estranho(self):
        q = self.post('/api/perguntas/listar', {'conta': 'ML19', 'situacao': 'respondidas'}).get_json()['perguntas'][0]
        self.assertEqual(q['resposta']['texto'], 'Sim.')
        self.assertEqual(q['item']['sku'], 'KIT_A, KIT_B')
        self.assertEqual((q['item']['thumb'], q['item']['link']), ('', ''))      # só foto do ML e link https
        self.assertEqual((q['comprador'], q['comprador_id']), ('', '502'))       # sem apelido: a tela usa o número

    def test_periodo_corta_as_antigas(self):
        d = self.post('/api/perguntas/listar', {'conta': 'ML19', 'situacao': 'todas', 'dias': 15}).get_json()
        self.assertEqual([q['id'] for q in d['perguntas']], ['111'])
        self.assertFalse(d['incompleto'])

    def test_produto_indisponivel_nao_esconde_a_pergunta(self):
        self.f.falha_itens = True
        d = self.post('/api/perguntas/listar', {'conta': 'ML19'}).get_json()
        self.assertEqual(d['perguntas'][0]['texto'], 'qual a gramatura?')
        self.assertIsNone(d['perguntas'][0]['item'])
        self.assertTrue(d['itens_falharam'])

    def test_mais_que_o_limite_avisa(self):
        for i in range(1000, 1205):
            self.f.questoes[str(i)] = {'id': i, 'seller_id': 99, 'item_id': 'MLB100000001', 'status': 'UNANSWERED', 'text': 'x', 'date_created': ha(minutes=i), 'from': {}, 'answer': None}
        d = self.post('/api/perguntas/listar', {'conta': 'ML19'}).get_json()
        self.assertEqual(len(d['perguntas']), 200)
        self.assertTrue(d['incompleto'])

    def test_sem_permissao_explica(self):
        self.f.negado = True
        r = self.post('/api/perguntas/listar', {'conta': 'ML19'})
        self.assertEqual(r.status_code, 503)
        self.assertIn('permissão de perguntas', r.get_json()['error'])

    def test_entradas_invalidas(self):
        for corpo in ({'conta': 'X'}, {'conta': 'ML19', 'situacao': 'x'}, {'conta': 'ML19', 'dias': 3}):
            self.assertEqual(self.post('/api/perguntas/listar', corpo).status_code, 400)


class TestPrazo(Base):
    def test_prazo(self):
        d = self.post('/api/perguntas/prazo', {'conta': 'ML19'}).get_json()
        self.assertEqual((d['total'], d['aumento']), (1309, 10))
        self.assertEqual([p['minutos'] for p in d['periodos']], [100, 45, 200])

    def test_conta_sem_dados(self):
        with mock.patch.object(self.f, 'tempo', mock.Mock(status_code=404)):
            d = self.post('/api/perguntas/prazo', {'conta': 'ML19'}).get_json()
        self.assertTrue(d['ok'])
        self.assertIsNone(d['total'])


class TestResponder(Base):
    def test_envia_e_confere(self):
        d = self.responder().get_json()
        self.assertEqual(d['status'], 'OK')
        self.assertEqual(self.f.posts, [(fontes.API + '/answers', {'question_id': 111, 'text': 'Gramatura de 150 g/m².'})])

    def test_exige_confirmacao_e_texto(self):
        self.assertEqual(self.responder(confirmacao='').status_code, 400)
        self.assertEqual(self.responder(texto='   ').status_code, 400)
        self.assertEqual(self.responder(texto='x' * 2001).status_code, 400)
        self.assertEqual(self.responder(pergunta='abc').status_code, 400)
        self.assertEqual(self.responder(conta='X').status_code, 400)
        self.assertEqual(self.f.posts, [])

    def test_limite_de_2000_passa(self):
        self.assertEqual(self.responder(texto='x' * 2000).get_json()['status'], 'OK')

    def test_ja_respondida_nao_envia(self):
        d = self.responder(pergunta='222').get_json()
        self.assertEqual(d['status'], 'PULADO')
        self.assertEqual(d['resposta']['texto'], 'Sim.')
        self.assertEqual(self.f.posts, [])

    def test_de_outra_conta_nao_envia(self):
        self.assertEqual(self.responder(pergunta='333').get_json()['status'], 'PULADO')
        self.assertEqual(self.f.posts, [])

    def test_recusa_mostra_motivo_e_deixa_tentar_de_novo(self):
        self.f.recusa = True
        d = self.responder().get_json()
        self.assertEqual(d['status'], 'ERRO')
        self.assertIn('dados de contato', d['mensagem'])
        self.f.recusa = False
        self.assertEqual(self.responder().get_json()['status'], 'OK')

    def test_queda_de_conexao_nao_repete(self):
        self.f.queda = True
        d = self.responder().get_json()
        self.assertEqual(d['status'], 'NAO_CONFIRMADO')
        self.assertEqual(len(self.f.posts), 1)
        self.assertEqual(self.responder().get_json()['status'], 'PULADO')       # bloqueio contra resposta dupla
        self.assertEqual(len(self.f.posts), 1)

    def test_queda_mas_a_resposta_chegou(self):
        original = self.f.post
        def chega_e_cai(url, json=None, **kw):
            original(url, json, **kw)
            raise requests.Timeout('demorou')
        with mock.patch.object(fontes._SESS, 'post', chega_e_cai):
            self.assertEqual(self.responder().get_json()['status'], 'OK')

    def test_aceita_mas_nao_aparece(self):
        self.f.ignora = True
        self.assertEqual(self.responder().get_json()['status'], 'NAO_CONFIRMADO')


class TestTela(Base):
    def test_aba_no_menu_e_foto_liberada(self):
        r = self.c.get('/perguntas')
        self.assertEqual(r.status_code, 200)
        self.assertIn('href="/perguntas"', r.get_data(as_text=True))
        self.assertIn('https://http2.mlstatic.com', r.headers['Content-Security-Policy'])

    def test_login_obrigatorio(self):
        anon = app_mod.app.test_client()
        self.assertEqual(anon.get('/perguntas').status_code, 302)
        self.assertEqual(anon.post('/api/perguntas/responder', json={}).status_code, 401)


if __name__ == '__main__':
    unittest.main()


class SkuEstranhoTest(unittest.TestCase):
    def test_sku_numerico_nao_derruba(self):
        corpo = {'id': 'MLB1', 'seller_custom_field': 12345, 'attributes': [], 'variations': []}
        self.assertEqual(perguntas._skus(corpo), ['12345'])
        self.assertEqual(perguntas._sku(corpo), '12345')
