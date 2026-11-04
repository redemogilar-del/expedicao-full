"""Conhecimento por SKU vindo da planilha do dono (Prompt Geral, Regras Gerais, Respostas Padrão, Informações por SKU e Base de Conhecimento
da família). Só LEITURA: o site nunca altera essa planilha; quem edita é você, lá. Sem Flask.

Apps Script da planilha (ação conhecimento_sku; trecho em apps_script/conhecimento_sku.gs). Variáveis: EXP_CONHECIMENTO_URL e EXP_CONHECIMENTO_KEY.
Ordem de prioridade para a IA (a mesma da planilha): Regras Especiais do SKU > Base de Conhecimento da família > anúncio.
"""
import os

import requests

import envios
import fontes

MAX_SKUS = 10
LIM = {'prompt': 4000, 'regra': 400, 'resposta': 600, 'especiais': 3000, 'topico': 80, 'base': 1500}
MAX_REGRAS, MAX_RESPOSTAS, MAX_TOPICOS = 60, 30, 20


def configurado():
    return os.environ.get('EXP_CONHECIMENTO_URL', '').strip().startswith('https://script.google.com/') and bool(os.environ.get('EXP_CONHECIMENTO_KEY', '').strip())


def _chamar(acao, **dados):
    url, chave = os.environ.get('EXP_CONHECIMENTO_URL', '').strip(), os.environ.get('EXP_CONHECIMENTO_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        raise fontes.FonteIndisponivel('A planilha de conhecimento ainda não está ligada (falta EXP_CONHECIMENTO_URL e EXP_CONHECIMENTO_KEY no Render).')
    try:
        r = fontes._SESS.post(url, json=dict(dados, chave=chave, acao=acao), timeout=(5, 35), allow_redirects=True)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise fontes.FonteIndisponivel('Não consegui falar com a planilha de conhecimento agora (%s). Tente de novo.' % str(exc)[:60])
    if not isinstance(d, dict) or d.get('ok') is not True:
        msg = str(d.get('error') or '')[:120] if isinstance(d, dict) else ''
        raise fontes.FonteIndisponivel('A planilha de conhecimento não aceitou o pedido%s. Confira se o trecho de apps_script/conhecimento_sku.gs foi colado, publicado numa nova versão, e se a chave é a mesma do Render.' % (' (' + msg + ')' if msg else ''))
    return d


def _txt(v, limite):
    return str(v or '').replace('\r\n', '\n').strip()[:limite]


def _lista(v, limite, maximo):
    return [t for t in (_txt(x, limite) for x in (v if isinstance(v, list) else [])) if t][:maximo]


def _limpar_skus(skus):
    if not isinstance(skus, list):
        raise envios.ArquivoInvalido('Lista de SKUs inválida.')
    saida = []
    for s in skus:
        s = str(s or '').strip()
        if s and len(s) <= 80 and s not in saida:
            saida.append(s)
    if len(saida) > MAX_SKUS:
        raise envios.ArquivoInvalido('SKUs demais de uma vez (máximo %d).' % MAX_SKUS)
    return saida


def ler(skus):
    """{'geral': {prompt, regras, respostas}, 'skus': {sku pedido: {encontrado, familia, regras_especiais, base_encontrada, base}}}.
    Falha vira FonteIndisponivel: nunca vira 'sem dados'."""
    lista = _limpar_skus(skus)
    d = _chamar('conhecimento_sku', skus=lista)
    g = d.get('geral') if isinstance(d.get('geral'), dict) else {}
    geral = {'prompt': _txt(g.get('prompt'), LIM['prompt']), 'regras': _lista(g.get('regras'), LIM['regra'], MAX_REGRAS),
             'respostas': _lista(g.get('respostas'), LIM['resposta'], MAX_RESPOSTAS)}
    dados = d.get('skus') if isinstance(d.get('skus'), dict) else {}
    saida = {}
    for s in lista:
        x = dados.get(s) if isinstance(dados.get(s), dict) else {}
        base = [{'topico': _txt(b.get('topico'), LIM['topico']), 'texto': _txt(b.get('texto'), LIM['base'])} for b in (x.get('base') if isinstance(x.get('base'), list) else []) if isinstance(b, dict) and _txt(b.get('texto'), 1)][:MAX_TOPICOS]
        saida[s] = {'encontrado': x.get('encontrado') is True, 'familia': _txt(x.get('familia'), 80), 'regras_especiais': _txt(x.get('regras_especiais'), LIM['especiais']),
                    'base_encontrada': x.get('base_encontrada') is True, 'base': base}
    return {'geral': geral, 'skus': saida}


def para_prompt(dados):
    """Texto para a IA, na ordem de prioridade da planilha. Vazio se não há nada útil."""
    g, partes = dados['geral'], []
    if g['prompt']:
        partes.append('INSTRUÇÕES DO DONO DA LOJA:\n' + g['prompt'])
    if g['regras']:
        partes.append('REGRAS GERAIS:\n' + '\n'.join('- ' + r for r in g['regras']))
    if g['respostas']:
        partes.append('RESPOSTAS PADRÃO (use quando a pergunta for sobre o tema):\n' + '\n\n'.join(g['respostas']))
    for sku, x in dados['skus'].items():
        if x['regras_especiais']:
            partes.append('REGRAS ESPECIAIS DO SKU %s (prioridade máxima):\n%s' % (sku, x['regras_especiais']))
        if x['base']:
            partes.append('BASE DE CONHECIMENTO DA FAMÍLIA %s (SKU %s):\n%s' % (x['familia'], sku, '\n'.join('%s: %s' % (b['topico'], b['texto']) for b in x['base'])))
    return '\n\n'.join(partes)
