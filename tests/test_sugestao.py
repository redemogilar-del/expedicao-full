import json
import os
import sys
import unittest
from unittest import mock

import test_perguntas
from test_perguntas import Base
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import conhecimento
import fontes
import sugestao

ITEM = {'id': 'MLB100000001', 'seller_custom_field': 'GEO2', 'title': 'Manta Geotêxtil Drenante 160m2', 'status': 'active', 'price': 651.25, 'available_quantity': 999, 'condition': 'new',
        'attributes': [{'name': 'Gramatura', 'value_name': '150 g/m²'}, {'name': 'Marca', 'value_name': ''}], 'shipping': {'free_shipping': True}}


class BaseIA(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('sugestao.time.sleep')
        p.start()
        self.addCleanup(p.stop)
        self.chamadas = []
        original = self.f.ml

        def ml(tk, caminho, params=None):
            if caminho == '/items/MLB100000001':
                return ITEM
            if caminho == '/items/MLB100000001/description':
                return {'plain_text': 'Manta de 160 m².'}
            return original(tk, caminho, params)
        self.f.ml = ml
        p = mock.patch.object(fontes, '_ml', ml); p.start(); self.addCleanup(p.stop)
        self.planilha = {'geral': {'prompt': 'Você é um especialista técnico da Rede Mogilar.', 'regras': ['Nunca invente informações.'], 'respostas': ['FRETE\nO valor é calculado pelo Mercado Livre conforme o CEP.']},
                         'skus': {'GEO2': {'encontrado': True, 'familia': 'GEO', 'regras_especiais': 'Este SKU possui 160 m².', 'base_encontrada': True, 'base': [{'topico': 'O QUE É', 'texto': 'Manta geotêxtil.'}]}}}
        p = mock.patch.object(conhecimento, 'ler', lambda skus: {'geral': self.planilha['geral'], 'skus': {s: self.planilha['skus'].get(s, {'encontrado': False, 'familia': '', 'regras_especiais': '', 'base_encontrada': False, 'base': []}) for s in skus}}); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {'EXP_GEMINI_KEY': 'chave-de-teste'}); p.start(); self.addCleanup(p.stop)

    def ia(self, bruto):
        def falso(contexto, pergunta):
            self.chamadas.append((contexto, pergunta)); return bruto
        p = mock.patch.object(sugestao, '_chamar_ia', falso); p.start(); self.addCleanup(p.stop)

    def sugerir(self, pergunta='111', conta='ML19'):
        return self.post('/api/perguntas/sugerir', {'conta': conta, 'pergunta': pergunta})


class TestSugerir(BaseIA):
    def test_devolve_rascunho_sem_enviar_nada(self):
        self.ia(json.dumps({'resposta': 'Olá! A gramatura é de 150 g/m². Qualquer dúvida, estamos à disposição.', 'falta': ''}))
        d = self.sugerir().get_json()
        self.assertTrue(d['ok'] and d['somente_leitura'])
        self.assertIn('150 g/m²', d['resposta'])
        self.assertEqual(self.f.posts, [])
        contexto, pergunta = self.chamadas[0]
        self.assertEqual(pergunta, 'qual a gramatura?')
        for trecho in ('Gramatura: 150 g/m²', '651.25', '999 unidades', 'Manta de 160 m²', 'Frete grátis: sim'):
            self.assertIn(trecho, contexto)
        self.assertNotIn('Marca', contexto)                       # atributo vazio fica de fora

    def test_conhecimento_da_planilha_vai_para_a_ia_na_ordem_certa(self):
        self.ia(json.dumps({'resposta': 'Olá! 160 m².', 'falta': ''}))
        self.sugerir()
        c = self.chamadas[0][0]
        for trecho in ('INSTRUÇÕES DO DONO DA LOJA:\nVocê é um especialista', '- Nunca invente informações.', 'RESPOSTAS PADRÃO', 'REGRAS ESPECIAIS DO SKU GEO2 (prioridade máxima):\nEste SKU possui 160 m²',
                       'BASE DE CONHECIMENTO DA FAMÍLIA GEO (SKU GEO2):\nO QUE É: Manta geotêxtil.'):
            self.assertIn(trecho, c)
        self.assertLess(c.index('REGRAS ESPECIAIS DO SKU'), c.index('BASE DE CONHECIMENTO DA FAMÍLIA'))

    def test_sku_fora_da_planilha_avisa(self):
        self.planilha['skus'] = {}
        self.ia(json.dumps({'resposta': 'Olá!', 'falta': ''}))
        d = self.sugerir().get_json()
        self.assertIn('não está na aba "Informações por sku"', d['falta'])
        self.assertNotIn('REGRAS ESPECIAIS', self.chamadas[0][0])
        self.assertIn('INSTRUÇÕES DO DONO', self.chamadas[0][0])        # as regras gerais continuam valendo

    def test_planilha_ilegivel_avisa_e_segue_so_com_o_anuncio(self):
        def quebra(skus):
            raise fontes.FonteIndisponivel('Não consegui falar com a planilha de conhecimento agora.')
        self.ia(json.dumps({'resposta': 'Olá!', 'falta': ''}))
        with mock.patch.object(conhecimento, 'ler', quebra):
            d = self.sugerir().get_json()
        self.assertEqual(d['resposta'], 'Olá!')
        self.assertIn('planilha de conhecimento', d['falta'])
        self.assertIn('usa só o anúncio', d['falta'])

    def test_aceita_json_dentro_de_bloco_de_codigo(self):
        self.ia('```json\n{"resposta": "Olá! Sim.", "falta": ""}\n```')
        self.assertEqual(self.sugerir().get_json()['resposta'], 'Olá! Sim.')

    def test_sem_informacao_vem_vazio_e_diz_o_que_conferir(self):
        self.ia(json.dumps({'resposta': '', 'falta': 'Peso da embalagem não está no anúncio.'}))
        d = self.sugerir().get_json()
        self.assertEqual((d['resposta'], d['falta']), ('', 'Peso da embalagem não está no anúncio.'))

    def test_contato_na_resposta_e_barrado(self):
        for texto in ('Chame no WhatsApp (11) 91234-5678', 'Escreva para joao@loja.com', 'Veja em www.loja.com.br', 'Ligue 11912345678'):
            self.ia(json.dumps({'resposta': texto, 'falta': ''}))
            d = self.sugerir().get_json()
            self.assertEqual(d['resposta'], '', texto)
            self.assertIn('contato', d['falta'])

    def test_medidas_nao_viram_telefone(self):
        self.assertFalse(sugestao.tem_contato('Mede 160 m² de área, 2,5 mm de espessura e 150 g/m² de gramatura.'))

    def test_texto_longo_demais_e_barrado(self):
        self.ia(json.dumps({'resposta': 'a' * 2001, 'falta': ''}))
        self.assertEqual(self.sugerir().get_json()['resposta'], '')

    def test_resposta_da_ia_ilegivel_da_erro_claro(self):
        self.ia('desculpe, não sei')
        r = self.sugerir()
        self.assertEqual(r.status_code, 503)
        self.assertIn('formato', r.get_json()['error'])

    def test_sem_chave_explica_o_que_fazer(self):
        with mock.patch.dict(os.environ, {'EXP_GEMINI_KEY': ''}):
            r = self.sugerir()
        self.assertEqual(r.status_code, 503)
        self.assertIn('EXP_GEMINI_KEY', r.get_json()['error'])

    def test_ja_respondida_e_de_outra_conta_nao_chamam_a_ia(self):
        self.ia(json.dumps({'resposta': 'x', 'falta': ''}))
        self.assertEqual(self.sugerir('222').status_code, 400)
        self.assertEqual(self.sugerir('333').status_code, 400)
        self.assertEqual(self.chamadas, [])

    def test_entradas_invalidas(self):
        self.assertEqual(self.sugerir('abc').status_code, 400)
        self.assertEqual(self.sugerir(conta='X').status_code, 400)

    def test_anuncio_ilegivel_nao_chama_a_ia(self):
        self.ia(json.dumps({'resposta': 'x', 'falta': ''}))
        def quebra(tk, caminho, params=None):
            if caminho == '/items/MLB100000001':
                raise RuntimeError('HTTP 500')
            return self.f.ml(tk, caminho, params)
        with mock.patch.object(fontes, '_ml', quebra):
            r = self.sugerir()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.chamadas, [])

    def test_login_obrigatorio(self):
        import app as app_mod
        self.assertEqual(app_mod.app.test_client().post('/api/perguntas/sugerir', json={}).status_code, 401)


class TestChamadaIA(unittest.TestCase):
    def _chamar(self, status=200, corpo=None, texto=''):
        resp = mock.Mock(status_code=status, ok=status < 400, text=texto, json=lambda: corpo or {})
        with mock.patch.dict(os.environ, {'EXP_GEMINI_KEY': 'k'}), mock.patch.object(sugestao.requests, 'post', return_value=resp) as p:
            try:
                return sugestao._chamar_ia('ctx', 'pergunta'), p
            except fontes.FonteIndisponivel as exc:
                return str(exc), p

    def test_envia_chave_no_cabecalho_modelo_e_pergunta_delimitada(self):
        texto, p = self._chamar(corpo={'candidates': [{'content': {'parts': [{'text': '{"resposta":"a","falta":""}'}]}}]})
        self.assertEqual(texto, '{"resposta":"a","falta":""}')
        self.assertEqual(p.call_args.args[0], sugestao.URL_IA % sugestao.MODELO_PADRAO)
        self.assertNotIn('key=', p.call_args.args[0])                       # a chave não vai no endereço
        kw = p.call_args.kwargs
        self.assertEqual(kw['headers']['x-goog-api-key'], 'k')
        self.assertEqual(kw['json']['generationConfig']['responseMimeType'], 'application/json')
        self.assertIn('<pergunta_do_cliente>\npergunta\n</pergunta_do_cliente>', kw['json']['contents'][0]['parts'][0]['text'])
        self.assertIn('RASCUNHOS', kw['json']['systemInstruction']['parts'][0]['text'])

    def test_modelo_pode_ser_trocado_pelo_ambiente(self):
        resp = mock.Mock(status_code=200, ok=True, json=lambda: {'candidates': [{'content': {'parts': [{'text': 'x'}]}}]})
        with mock.patch.dict(os.environ, {'EXP_GEMINI_KEY': 'k', 'EXP_IA_MODELO': 'gemini-novo'}), mock.patch.object(sugestao.requests, 'post', return_value=resp) as p:
            sugestao._chamar_ia('c', 'p')
        self.assertTrue(p.call_args.args[0].endswith('/gemini-novo:generateContent'))
        with mock.patch.dict(os.environ, {'EXP_GEMINI_KEY': 'k', 'EXP_IA_MODELO': 'a/b?x'}):
            with self.assertRaises(fontes.FonteIndisponivel):
                sugestao._chamar_ia('c', 'p')

    def test_erros_explicam_o_que_fazer(self):
        self.assertIn('recusou a chave', self._chamar(status=400, texto='API key not valid')[0])
        self.assertIn('EXP_IA_MODELO', self._chamar(status=404)[0])
        self.assertIn('HTTP 503', self._chamar(status=503)[0])
        self.assertIn('à mão', self._chamar(corpo={'promptFeedback': {'blockReason': 'SAFETY'}})[0])
        self.assertIn('não devolveu texto', self._chamar(corpo={'candidates': []})[0])


if __name__ == '__main__':
    unittest.main()
