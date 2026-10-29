"""Preço necessário para atingir a margem e atualização de preço no Mercado Livre (aba Vendas do dia).

Margem sobre o custo (a mesma da tela):  margem = (preço − tarifa ML − frete − imposto%×preço − custo) ÷ custo

Tarifa e frete vêm da API do ML NO PREÇO AVALIADO (não do preço da venda), como no painel de ajuste de margem.
Falha de consulta nunca vira zero: o anúncio fica bloqueado com o motivo.

Alterar preço (aplicar) é a única escrita no Mercado Livre deste painel: exige seleção + confirmação,
reanalisa na hora, não aceita se algo mudou desde o que a pessoa viu e confere o preço depois do PUT.
Só sobe preço (o objetivo é atingir a margem); anúncio que já atinge a margem não é alterado.
"""
import re
import threading
import time
from decimal import Decimal, ROUND_CEILING

import requests

import fontes

D = Decimal
TIPOS = {'gold_pro': 'Premium', 'gold_special': 'Clássico'}
CONFIRMACAO = 'APLICAR_PRECO_VENDAS'
LIMITE_VARIACAO_PCT = D('15')        # subir mais que isso exige liberação extra
DIVERGENCIA_MAX_PCT = D('1')         # preço novo recalculado × o que a pessoa viu
PRECO_MIN, PRECO_MAX = D('1'), D('1000000')
MAX_APLICAR = 5
JANELA_REAPLICACAO = 90              # segundos: evita clique duplo no mesmo anúncio
_RECENTES, _LOCK = {}, threading.Lock()
_MLB = re.compile(r'^MLB\d{6,14}$')


class Bloqueio(Exception):
    """Motivo, em português simples, pelo qual o anúncio não pode ser calculado ou alterado."""


def _cent(v):
    return v.quantize(D('0.01'), rounding=ROUND_CEILING)


def _f(v, casas=2):
    return None if v is None else round(float(v), casas)


def _dec(v):
    n = fontes._numero(v)
    return None if n is None else D(str(n))


def tarifa_api(tk, item, preco):
    """Tarifa de venda (R$) que o ML cobraria se o anúncio custasse `preco`."""
    sh = item.get('shipping') or {}
    params = {'price': str(preco), 'category_id': item.get('category_id'), 'listing_type_id': item.get('listing_type_id'), 'currency_id': item.get('currency_id') or 'BRL',
              'shipping_mode': sh.get('mode'), 'logistic_type': sh.get('logistic_type')}
    params = {k: v for k, v in params.items() if v not in (None, '')}
    if not params.get('category_id') or not params.get('listing_type_id'):
        raise Bloqueio('Categoria ou tipo de anúncio ausente')
    try:
        resp = fontes._ml(tk, '/sites/MLB/listing_prices', params)
    except RuntimeError as exc:
        raise Bloqueio('Falha ao consultar a tarifa do ML: ' + str(exc)[:80])
    opcoes = [resp] if isinstance(resp, dict) else (resp if isinstance(resp, list) else [])
    opcoes = [x for x in opcoes if isinstance(x, dict) and x.get('listing_type_id') == item.get('listing_type_id') and x.get('sale_fee_amount') is not None]
    if len(opcoes) != 1:
        raise Bloqueio('A API não devolveu uma tarifa única para este anúncio')
    t = _dec(opcoes[0]['sale_fee_amount'])
    if t is None or t < 0 or t >= preco:
        raise Bloqueio('Tarifa do ML inválida')
    return t


def frete_api(tk, item, vendedor, preco):
    """Frete pago pelo vendedor se o anúncio custasse `preco` (0 se o comprador paga)."""
    sh = item.get('shipping') or {}
    gratis = sh.get('free_shipping')
    if not isinstance(gratis, bool):
        raise Bloqueio('Frete grátis não identificado')
    if gratis is False:
        return D('0')
    if sh.get('mode') not in ('me1', 'me2'):
        raise Bloqueio('Modalidade de envio não suportada (entrega a combinar)')
    params = {'item_id': item['id'], 'item_price': str(preco), 'listing_type_id': item.get('listing_type_id'), 'mode': sh.get('mode'),
              'logistic_type': sh.get('logistic_type'), 'free_shipping': 'true', 'verbose': 'true'}
    try:
        resp = fontes._ml(tk, '/users/%s/shipping_options/free' % vendedor, {k: v for k, v in params.items() if v not in (None, '')})
    except RuntimeError as exc:
        raise Bloqueio('Falha ao cotar o frete: ' + str(exc)[:80])
    pais = ((resp.get('coverage') or {}).get('all_country') or {}) if isinstance(resp, dict) else {}
    custo = _dec(pais.get('list_cost'))
    if pais.get('currency_id') != 'BRL' or custo is None or custo < 0:
        raise Bloqueio('Cotação de frete inválida')
    return custo


def margem_no_preco(custo, preco, tarifa, frete, imposto_pct):
    return (preco - tarifa - frete - preco * imposto_pct / 100 - custo) / custo * 100


def preco_alvo(custo, meta_pct, imposto_pct, tarifa_fn, frete_fn, inicio, max_voltas=10):
    """Menor preço (centavos) cuja margem REAL (tarifa e frete a esse preço) atinge a meta. Retorna (preco, tarifa, frete, margem)."""
    fator = 1 - imposto_pct / 100
    if fator <= 0 or custo <= 0 or meta_pct < 0:
        raise Bloqueio('Dados inválidos para o cálculo (custo, margem ou imposto)')
    p = inicio
    for _ in range(max_voltas):
        t, f = tarifa_fn(p), frete_fn(p)
        m = margem_no_preco(custo, p, t, f, imposto_pct)
        if m >= meta_pct:
            return p, t, f, m
        p = max(_cent((custo * (1 + meta_pct / 100) + f + t) / fator), p + D('0.01'))
        if p > PRECO_MAX:
            break
    raise Bloqueio('O preço não estabilizou (tarifa e frete mudam com o preço). Revise na mão.')


def buscar_item(tk, mlb):
    item = fontes._ml(tk, '/items/' + mlb)
    if not isinstance(item, dict) or str(item.get('id')) != mlb:
        raise Bloqueio('A API não retornou este anúncio')
    return item


def analisar(tk, vendedor, item, custo, meta, imposto_pct):
    """Preço necessário para chegar na margem `meta` (%). Devolve um dicionário; `acao` = 'subir' | 'ok'. Bloqueio vira exceção."""
    if str(item.get('seller_id')) != str(vendedor):
        raise Bloqueio('Anúncio não pertence à conta')
    if item.get('status') not in ('active', 'paused'):
        raise Bloqueio('Anúncio %s: não dá para alterar o preço' % (item.get('status') or 'sem status'))
    if item.get('listing_type_id') not in TIPOS:
        raise Bloqueio('Tipo de anúncio não suportado (só Clássico e Premium)')
    atual = _dec(item.get('price'))
    if atual is None or atual <= 0:
        raise Bloqueio('Preço atual inválido')
    variacoes = [v for v in item.get('variations') or [] if isinstance(v, dict)]
    if variacoes:
        if any(v.get('id') is None for v in variacoes):
            raise Bloqueio('Variação sem identificador')
        precos = {_dec(v['price']) if v.get('price') is not None else atual for v in variacoes}
        if len(precos) != 1 or atual not in precos:
            raise Bloqueio('Preços diferentes por variação: altere direto no Mercado Livre')
    custo, meta, imposto_pct = D(str(custo)) if custo is not None else None, D(str(meta)) if meta is not None else None, D(str(imposto_pct))
    if custo is None or custo <= 0 or meta is None or meta < 0 or meta >= 1000:
        raise Bloqueio('Falta custo ou margem (preencha na tela)')
    cache_t, cache_f = {}, {}

    def tarifa(p):
        if p not in cache_t:
            cache_t[p] = tarifa_api(tk, item, p)
        return cache_t[p]

    def frete(p):
        if p not in cache_f:
            cache_f[p] = frete_api(tk, item, vendedor, p)
        return cache_f[p]
    t0, f0 = tarifa(atual), frete(atual)
    m0 = margem_no_preco(custo, atual, t0, f0, imposto_pct)
    reg = {'mlb': item['id'], 'tipo': TIPOS[item['listing_type_id']], 'status_anuncio': item.get('status'), 'preco_atual': _f(atual), 'margem_atual': _f(m0), 'custo': _f(custo), 'meta': _f(meta),
           'tarifa': _f(t0), 'frete': _f(f0), 'acao': 'ok', 'preco_novo': None, 'variacao_pct': None, 'margem_nova': None, 'requer_liberacao': False,
           'mensagem': 'O preço atual do anúncio já atinge a margem (a venda pode ter sido com desconto ou promoção).'}
    if m0 >= meta:
        return reg
    novo, t1, f1, m1 = preco_alvo(custo, meta, imposto_pct, tarifa, frete, atual)
    if novo < PRECO_MIN or novo > PRECO_MAX or novo <= atual:
        raise Bloqueio('Cálculo inconsistente. Revise na mão.')
    variacao = (novo / atual - 1) * 100
    reg.update(acao='subir', preco_novo=_f(novo), variacao_pct=_f(variacao), margem_nova=_f(m1), tarifa_nova=_f(t1), frete_novo=_f(f1), requer_liberacao=variacao > LIMITE_VARIACAO_PCT,
               mensagem='Subir de R$ %.2f para R$ %.2f (+%.1f%%) para chegar a %.1f%% de margem.' % (atual, novo, variacao, meta) +
                        (' Aumento acima de %s%%: exige liberação extra.' % LIMITE_VARIACAO_PCT if variacao > LIMITE_VARIACAO_PCT else ''))
    return reg


def reservar(conta, mlb):
    """Impede aplicar o mesmo anúncio duas vezes seguidas (clique duplo / duas abas)."""
    agora = time.monotonic()
    with _LOCK:
        for k in [k for k, t in _RECENTES.items() if agora - t > JANELA_REAPLICACAO]:
            _RECENTES.pop(k, None)
        if (conta, mlb) in _RECENTES:
            return False
        _RECENTES[(conta, mlb)] = agora
        return True


def enviar_preco(tk, item, novo):
    """PUT do preço e conferência. Retorna (ok, preço lido no ML)."""
    mlb = item['id']
    variacoes = [v for v in item.get('variations') or [] if isinstance(v, dict)]
    corpo = {'variations': [{'id': v['id'], 'price': float(novo)} for v in variacoes]} if variacoes else {'price': float(novo)}
    resp = fontes._SESS.put(fontes.API + '/items/' + mlb, json=corpo, headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 25))
    if not resp.ok:
        raise RuntimeError('O Mercado Livre recusou a alteração (HTTP %s): %s' % (resp.status_code, resp.text[:160]))
    conf = fontes._ml(tk, '/items/' + mlb)
    if variacoes:
        precos = [v.get('price') for v in conf.get('variations') or []]
        ok = len(precos) == len(variacoes) and all(p is not None and abs(D(str(p)) - novo) < D('0.011') for p in precos)
    else:
        ok = conf.get('price') is not None and abs(D(str(conf['price'])) - novo) < D('0.011')
    return ok, conf.get('price')
