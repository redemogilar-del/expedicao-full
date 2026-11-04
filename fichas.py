"""Fichas por SKU: informações que o dono escreve (gramatura, medidas, o que acompanha...) e a IA usa para sugerir respostas. Sem Flask.

Ficam na aba "Fichas SKU" da planilha (Apps Script, ações fichas_sku_ler / fichas_sku_salvar; trecho em apps_script/fichas_sku.gs).
Escrita só na planilha do dono, com confirmação, e conferida lendo de volta. SKU comparado sem diferença de maiúsculas.
"""
import os

import requests

import envios
import fontes

CONFIRMACAO = 'SALVAR_FICHA'
MAX_TEXTO = 2000
MAX_SKUS = 20


def _chamar(acao, **dados):
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        raise fontes.FonteIndisponivel('A planilha não está configurada (EXP_SHEETS_URL e EXP_SHEETS_KEY no Render).')
    try:
        r = fontes._SESS.post(url, json=dict(dados, chave=chave, acao=acao), timeout=(5, 35), allow_redirects=True)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise fontes.FonteIndisponivel('Não consegui falar com a planilha agora (%s). Tente de novo.' % str(exc)[:60])
    if not isinstance(d, dict) or d.get('ok') is not True:
        raise fontes.FonteIndisponivel('A planilha não aceitou o pedido. Falta colar o trecho de apps_script/fichas_sku.gs no Apps Script e publicar uma nova versão.')
    return d


def chave(sku):
    return str(sku or '').strip().lower()


def _limpar_skus(skus):
    if not isinstance(skus, list):
        raise envios.ArquivoInvalido('Lista de SKUs inválida.')
    saida = []
    for s in skus:
        s = str(s or '').strip()
        if s and s not in saida:
            saida.append(s)
    if len(saida) > MAX_SKUS:
        raise envios.ArquivoInvalido('SKUs demais de uma vez (máximo %d).' % MAX_SKUS)
    return saida


def ler(skus):
    """{sku pedido: {'texto', 'atualizado'}} só dos que têm ficha. Falha vira FonteIndisponivel (nunca 'sem ficha')."""
    lista = _limpar_skus(skus)
    if not lista:
        return {}
    achadas = _chamar('fichas_sku_ler', skus=lista).get('fichas')
    achadas = achadas if isinstance(achadas, dict) else {}
    saida = {}
    for s in lista:
        f = achadas.get(chave(s))
        if isinstance(f, dict) and str(f.get('texto') or '').strip():
            saida[s] = {'texto': str(f['texto']), 'atualizado': str(f.get('atualizado') or '')}
    return saida


def validar_salvar(c):
    if c.get('confirmacao') != CONFIRMACAO:
        raise envios.ArquivoInvalido('Confirmação obrigatória para salvar a ficha.')
    sku = str(c.get('sku') or '').strip()
    if not sku or len(sku) > 80 or '\n' in sku or '\r' in sku:
        raise envios.ArquivoInvalido('SKU inválido.')
    texto = str(c.get('texto') or '').replace('\r\n', '\n').strip()
    if len(texto) > MAX_TEXTO:
        raise envios.ArquivoInvalido('A ficha passou de %d caracteres. Encurte o texto.' % MAX_TEXTO)
    return sku, texto


def salvar(sku, texto):
    """Grava a ficha e confere lendo de volta. status: OK / NAO_CONFIRMADO. Texto vazio apaga as informações do SKU."""
    _chamar('fichas_sku_salvar', sku=sku, texto=texto)
    try:
        depois = ler([sku]).get(sku, {}).get('texto', '')
    except fontes.FonteIndisponivel:
        return {'sku': sku, 'status': 'NAO_CONFIRMADO', 'texto': texto, 'mensagem': 'Enviei para a planilha, mas não consegui conferir. Abra a ficha de novo para ver o que ficou salvo.'}
    if depois.strip() == texto:
        return {'sku': sku, 'status': 'OK', 'texto': depois, 'mensagem': 'Ficha salva na planilha e conferida.' if texto else 'Ficha apagada.'}
    return {'sku': sku, 'status': 'NAO_CONFIRMADO', 'texto': depois, 'mensagem': 'A planilha não mostra o texto que você enviou. Abra a ficha de novo e confira.'}
