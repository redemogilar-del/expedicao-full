"""Loja da Shopee (aba Shopee): pedidos a enviar, lucro de cada venda e preço necessário. Sem Flask.

Fonte: API oficial da Shopee (Open Platform v2, host do Brasil). O token da loja fica num Apps Script próprio
(apps_script/shopee_token.gs), que guarda o refresh_token e devolve um access_token válido — o site não grava nada.

Lucro de cada venda = líquido que a Shopee vai pagar (escrow_amount, já sem comissão, taxa de serviço, cupom da loja e
frete que ficou com você) − imposto (venda × %) − custo × quantidade. É o mesmo cálculo da aba Vendas do dia do ML:
tarifa = venda − líquido, frete = 0 (já dentro do líquido). Pedido com vários itens: o líquido é dividido entre os
itens pelo valor de cada um. Sem o líquido (a Shopee não respondeu), a linha fica "Sem dados" — nunca vira zero.

Preço necessário: a Shopee cobra comissão + taxa fixa por item conforme a faixa de preço (TABELA_TARIFA). Para cada
faixa calcula o menor preço que atinge a margem e fica com o menor que cabe na faixa.

Alterar preço é a única escrita na Shopee: exige confirmação, até MAX_APLICAR por vez, relê o preço na hora, não aceita
anúncio em promoção, só sobe, liberação extra acima de 15% e confere depois (get_model_list).
"""
import hashlib
import hmac
import os
import re
import threading
import time
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo

import requests

import fontes

D = Decimal
CONTA = 'SHOPEE'
HOST_PADRAO = 'https://openplatform.shopee.com.br'
CONFIRMACAO = 'APLICAR_PRECO_SHOPEE'
MAX_APLICAR = 5
LIMITE_VARIACAO_PCT = D('15')
DIVERGENCIA_MAX_PCT = D('1')
PRECO_MIN, PRECO_MAX = D('1'), D('1000000')
JANELA_REAPLICACAO = 90
STATUS_A_ENVIAR = ('READY_TO_SHIP', 'PROCESSED')
STATUS_NOME = {'UNPAID': 'Não pago', 'READY_TO_SHIP': 'A enviar', 'PROCESSED': 'Etiqueta gerada', 'SHIPPED': 'Enviado', 'TO_CONFIRM_RECEIVE': 'Entregue',
               'COMPLETED': 'Concluído', 'IN_CANCEL': 'Cancelando', 'CANCELLED': 'Cancelado', 'TO_RETURN': 'Devolução', 'RETRY_SHIP': 'Reenvio', 'INVALID': 'Inválido'}
# Comissão + taxa fixa por item, por faixa do preço do item (vendedor CNPJ, Programa de Frete Grátis, 2026).
# Fonte: tabela pública da Shopee divulgada em 2026 — confira com a "renda" real dos pedidos (a tela mostra as duas).
TABELA_TARIFA = (                      # (de, até (exclusive), comissão %, taxa fixa R$)
    (D('0'), D('80'), D('20'), D('4')),
    (D('80'), D('100'), D('14'), D('16')),
    (D('100'), D('200'), D('14'), D('20')),
    (D('200'), PRECO_MAX + 1, D('14'), D('26')),
)
_ORDER_SN = re.compile(r'^[0-9A-Z]{10,20}$')
_SESS = requests.Session()
_LOCK = threading.Lock()
_TOKEN = {'valor': None, 'shop_id': None, 'ate': 0.0}
_RECENTES = {}
FUSO = ZoneInfo('America/Sao_Paulo')


def _quando(ts):
    try:
        return datetime.fromtimestamp(int(ts), FUSO).strftime('%Y-%m-%d %H:%M') if ts else ''
    except (TypeError, ValueError, OSError):
        return ''


class Bloqueio(Exception):
    """Motivo, em português simples, pelo qual o anúncio não pode ser calculado ou alterado."""


class ShopeeIndisponivel(fontes.FonteIndisponivel):
    """A API da Shopee (ou o Apps Script do token) não respondeu como esperado."""


def configurado():
    return bool(os.getenv('EXP_SHOPEE_PARTNER_ID', '').strip().isdigit() and os.getenv('EXP_SHOPEE_PARTNER_KEY', '').strip()
                and os.getenv('EXP_SHOPEE_TOKEN_URL', '').strip().startswith('https://script.google.com/') and os.getenv('EXP_SHOPEE_TOKEN_KEY', '').strip())


def _host():
    h = os.getenv('EXP_SHOPEE_HOST', '').strip().rstrip('/') or HOST_PADRAO
    if not h.startswith('https://'):
        raise ShopeeIndisponivel('EXP_SHOPEE_HOST inválido.')
    return h


def assinar(chave, partner_id, caminho, ts, token='', shop_id=''):
    """sign = HMAC-SHA256(partner_key, partner_id + caminho + timestamp + access_token + shop_id), em hexadecimal."""
    base = '%s%s%s%s%s' % (partner_id, caminho, ts, token or '', shop_id or '')
    return hmac.new(chave.encode(), base.encode(), hashlib.sha256).hexdigest()


def token():
    """(access_token, shop_id) da loja, pedido ao Apps Script do token (cache até 5 min antes de vencer)."""
    if not configurado():
        raise ShopeeIndisponivel('A Shopee ainda não está ligada (falta EXP_SHOPEE_PARTNER_ID, EXP_SHOPEE_PARTNER_KEY, EXP_SHOPEE_TOKEN_URL e EXP_SHOPEE_TOKEN_KEY no Render).')
    with _LOCK:
        if _TOKEN['valor'] and time.time() < _TOKEN['ate']:
            return _TOKEN['valor'], _TOKEN['shop_id']
    try:
        r = _SESS.post(os.environ['EXP_SHOPEE_TOKEN_URL'].strip(), json={'chave': os.environ['EXP_SHOPEE_TOKEN_KEY'].strip(), 'acao': 'token'},
                       timeout=(5, 30), allow_redirects=True)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise ShopeeIndisponivel('Não consegui pegar o acesso da Shopee agora (%s).' % str(exc)[:80])
    if not isinstance(d, dict) or d.get('ok') is not True or not d.get('access_token') or not str(d.get('shop_id') or '').isdigit():
        raise ShopeeIndisponivel('O acesso da Shopee não veio: %s' % str((d or {}).get('erro') if isinstance(d, dict) else d)[:160])
    validade = max(60.0, float(d.get('expira_em_s') or 3600) - 300)
    with _LOCK:
        _TOKEN.update(valor=str(d['access_token']), shop_id=str(d['shop_id']), ate=time.time() + validade)
    return _TOKEN['valor'], _TOKEN['shop_id']


def chamar(caminho, params=None, corpo=None, tentativas=3):
    """Chamada assinada de loja. GET (params) ou POST (corpo). Erro da Shopee vira ShopeeIndisponivel com a mensagem dela."""
    tk, shop = token()
    pid, chave = os.environ['EXP_SHOPEE_PARTNER_ID'].strip(), os.environ['EXP_SHOPEE_PARTNER_KEY'].strip()
    for n in range(tentativas):
        ts = int(time.time())
        comuns = {'partner_id': pid, 'timestamp': ts, 'access_token': tk, 'shop_id': shop, 'sign': assinar(chave, pid, caminho, ts, tk, shop)}
        try:
            if corpo is None:
                r = _SESS.get(_host() + caminho, params=dict(comuns, **(params or {})), timeout=(5, 25))
            else:
                r = _SESS.post(_host() + caminho, params=comuns, json=corpo, timeout=(5, 25))
        except requests.RequestException as exc:
            if n + 1 < tentativas and corpo is None:
                time.sleep(1 + n)
                continue
            raise ShopeeIndisponivel('A Shopee não respondeu (%s).' % str(exc)[:80])
        if r.status_code in (429, 500, 502, 503, 504) and n + 1 < tentativas and corpo is None:
            time.sleep(1.5 * (n + 1))
            continue
        try:
            d = r.json()
        except ValueError:
            raise ShopeeIndisponivel('Resposta inválida da Shopee (HTTP %s).' % r.status_code)
        if d.get('error'):
            if d.get('error') in ('error_auth', 'invalid_acceess_token', 'invalid_access_token') and n == 0:
                with _LOCK:
                    _TOKEN.update(valor=None, ate=0.0)        # token vencido: pede outro ao Apps Script e tenta de novo
                tk, shop = token()
                continue
            raise ShopeeIndisponivel('A Shopee recusou (%s): %s' % (d.get('error'), str(d.get('message') or '')[:160]))
        return d.get('response') if 'response' in d else d
    raise ShopeeIndisponivel('A Shopee pediu para esperar. Tente de novo em 1 minuto.')


# ------------------------------------------------------------------ pedidos e renda (somente leitura)
def pedidos_a_enviar(dias=15):
    """[(order_sn, status)] dos pedidos pagos ainda não enviados (A enviar e Etiqueta gerada), criados nos últimos `dias`."""
    agora = int(time.time())
    saida, vistos = [], set()
    for status in STATUS_A_ENVIAR:
        fim = agora
        while fim > agora - dias * 86400:
            ini = max(agora - dias * 86400, fim - 15 * 86400)        # a Shopee aceita no máximo 15 dias por consulta
            cursor = ''
            for _ in range(50):
                r = chamar('/api/v2/order/get_order_list', {'time_range_field': 'create_time', 'time_from': ini, 'time_to': fim, 'page_size': 100,
                                                            'cursor': cursor, 'order_status': status, 'response_optional_fields': 'order_status'}) or {}
                for o in r.get('order_list') or []:
                    sn = str(o.get('order_sn') or '')
                    if sn and sn not in vistos:
                        vistos.add(sn)
                        saida.append((sn, str(o.get('order_status') or status)))
                if not r.get('more'):
                    break
                cursor = str(r.get('next_cursor') or '')
            fim = ini
    return saida


def detalhes(sns):
    """{order_sn: pedido} com itens (get_order_detail, 50 por vez)."""
    saida = {}
    for i in range(0, len(sns), 50):
        r = chamar('/api/v2/order/get_order_detail', {'order_sn_list': ','.join(sns[i:i + 50]),
                                                     'response_optional_fields': 'item_list,total_amount,order_status,ship_by_date,create_time'}) or {}
        for o in r.get('order_list') or []:
            saida[str(o.get('order_sn'))] = o
    return saida


def rendas(sns):
    """{order_sn: order_income} — o que a Shopee vai pagar e o que descontou (get_escrow_detail_batch, 50 por vez).
    Pedido que a Shopee não devolver fica de fora (vira "Sem dados")."""
    saida = {}
    for i in range(0, len(sns), 50):
        try:
            r = chamar('/api/v2/payment/get_escrow_detail_batch', corpo={'order_sn_list': sns[i:i + 50]})
        except ShopeeIndisponivel:
            continue
        for x in r if isinstance(r, list) else (r or {}).get('escrow_detail_list') or []:
            det = (x or {}).get('escrow_detail') or {}
            if det.get('order_sn') and isinstance(det.get('order_income'), dict):
                saida[str(det['order_sn'])] = det['order_income']
    return saida


def _n(v):
    try:
        return None if v is None or v == '' or isinstance(v, bool) else D(str(v))
    except Exception:
        return None


def _dois(v):
    return None if v is None else float(v.quantize(D('0.01')))


def linhas_dos_pedidos(pedidos, det, renda):
    """Uma linha por item, no formato da aba Vendas do dia (vendas.analisar): venda, tarifa (venda − líquido), frete 0."""
    saida = []
    for sn, status in pedidos:
        o = det.get(sn)
        if not o:
            continue
        inc = renda.get(sn) or {}
        itens = [it for it in o.get('item_list') or [] if isinstance(it, dict)]
        por_item = {}                                        # (item_id, model_id) → valor do item na renda
        for it in inc.get('items') or []:
            if isinstance(it, dict):
                por_item[(str(it.get('item_id')), str(it.get('model_id') or 0))] = _n(it.get('discounted_price'))
        valores = []
        for it in itens:
            q = int(it.get('model_quantity_purchased') or 0)
            v = por_item.get((str(it.get('item_id')), str(it.get('model_id') or 0)))
            if v is None:
                p = _n(it.get('model_discounted_price'))
                v = p * q if p is not None else None
            valores.append(v)
        total = sum(v for v in valores if v is not None) if all(v is not None for v in valores) else None
        liquido = _n(inc.get('escrow_amount'))
        comissao, servico = _n(inc.get('commission_fee')), _n(inc.get('service_fee'))
        cupom_loja = (_n(inc.get('voucher_from_seller')) or D(0)) + (_n(inc.get('seller_discount')) or D(0))
        frete_vend = _n(inc.get('final_shipping_fee'))
        prazo = o.get('ship_by_date')
        for it, v in zip(itens, valores):
            q = int(it.get('model_quantity_purchased') or 0)
            parte = (v / total) if (v is not None and total) else None
            liq = liquido * parte if (liquido is not None and parte is not None) else None
            modelo = str(it.get('model_id') or 0)
            saida.append({
                'conta': CONTA, 'pedido': sn, 'grupo': 'a_enviar', 'status_envio': status, 'status_rotulo': STATUS_NOME.get(status, status),
                'data': _quando(o.get('create_time')), 'limite_dt': _quando(prazo),
                'mlb': str(it.get('item_id') or ''), 'modelo': modelo, 'sku': str(it.get('model_sku') or it.get('item_sku') or '').strip(),
                'titulo': str(it.get('item_name') or '')[:200], 'variacao': str(it.get('model_name') or '')[:80], 'quantidade': q,
                'venda': _dois(v), 'preco_unit': _dois(v / q) if (v is not None and q) else None,
                'tarifa': _dois(v - liq) if (v is not None and liq is not None) else None, 'frete': 0.0 if liq is not None else None,
                'liquido': _dois(liq), 'comissao': _dois(comissao * parte) if (comissao is not None and parte is not None) else None,
                'servico': _dois(servico * parte) if (servico is not None and parte is not None) else None,
                'cupom_loja': _dois(cupom_loja * parte) if parte is not None else None,
                'frete_vendedor': _dois(frete_vend * parte) if (frete_vend is not None and parte is not None) else None,
                'obs': '' if liq is not None else 'A Shopee ainda não informou a renda deste pedido',
            })
    return saida


# ------------------------------------------------------------------ preço necessário
def tarifa_tabela(preco):
    """(comissão %, taxa fixa) da faixa do preço."""
    for de, ate, pct, fixa in TABELA_TARIFA:
        if de <= preco < ate:
            return pct, fixa
    raise Bloqueio('Preço fora da tabela de tarifas')


def margem_no_preco(custo, preco, imposto_pct):
    pct, fixa = tarifa_tabela(preco)
    return (preco - preco * pct / 100 - fixa - preco * imposto_pct / 100 - custo) / custo * 100


def preco_alvo(custo, meta_pct, imposto_pct):
    """Menor preço (centavos, para cima) que atinge a margem com a tarifa da faixa desse preço."""
    if custo is None or custo <= 0 or meta_pct is None or meta_pct < 0 or imposto_pct is None:
        raise Bloqueio('Falta custo ou margem (preencha na tela)')
    melhores = []
    for de, ate, pct, fixa in TABELA_TARIFA:
        fator = 1 - pct / 100 - imposto_pct / 100
        if fator <= 0:
            continue
        p = max(de, (custo * (1 + meta_pct / 100) + fixa) / fator).quantize(D('0.01'), rounding=ROUND_CEILING)
        if p < ate:
            melhores.append(p)
    if not melhores:
        raise Bloqueio('Não achei um preço que atinja a margem')
    return min(melhores)


def modelo_atual(item_id, modelo):
    """(preço atual, preço original, em promoção) do modelo (0 = anúncio sem variação)."""
    r = chamar('/api/v2/product/get_model_list', {'item_id': int(item_id)}) or {}
    modelos = [m for m in r.get('model') or [] if isinstance(m, dict)]
    if str(modelo) == '0':
        if modelos:
            raise Bloqueio('O anúncio tem variações; não identifiquei qual foi vendida')
        base = chamar('/api/v2/product/get_item_base_info', {'item_id_list': str(item_id)}) or {}
        it = next((x for x in base.get('item_list') or [] if str(x.get('item_id')) == str(item_id)), None)
        info = (it or {}).get('price_info') or []
        promo = bool((it or {}).get('promotion_id'))
    else:
        m = next((x for x in modelos if str(x.get('model_id')) == str(modelo)), None)
        if m is None:
            raise Bloqueio('A variação vendida não existe mais no anúncio')
        info, promo = m.get('price_info') or [], bool(m.get('has_promotion') or m.get('promotion_id'))
    if not info:
        raise Bloqueio('A Shopee não informou o preço do anúncio')
    atual, original = _n(info[0].get('current_price')), _n(info[0].get('original_price'))
    if atual is None or original is None or atual <= 0:
        raise Bloqueio('Preço do anúncio inválido')
    return atual, original, promo


def analisar_preco(item_id, modelo, custo, meta, imposto_pct):
    """Preço necessário para o anúncio (item_id + modelo) chegar na margem. acao = 'subir' | 'ok'."""
    atual, original, promo = modelo_atual(item_id, modelo)
    reg = {'mlb': str(item_id), 'modelo': str(modelo), 'preco_atual': _dois(atual), 'preco_original': _dois(original), 'em_promocao': promo,
           'custo': _dois(custo), 'meta': _dois(meta), 'margem_atual': _dois(margem_no_preco(custo, atual, imposto_pct)), 'acao': 'ok',
           'preco_novo': None, 'variacao_pct': None, 'margem_nova': None, 'requer_liberacao': False}
    novo = preco_alvo(custo, meta, imposto_pct)
    if novo <= atual:
        reg['mensagem'] = 'O preço atual do anúncio já atinge a margem (a venda pode ter sido com desconto ou cupom).'
        return reg
    if promo:
        raise Bloqueio('Anúncio em promoção na Shopee: altere o preço lá (a promoção usa outro preço)')
    variacao = (novo / atual - 1) * 100
    pct, fixa = tarifa_tabela(novo)
    reg.update(acao='subir', preco_novo=_dois(novo), variacao_pct=_dois(variacao), margem_nova=_dois(margem_no_preco(custo, novo, imposto_pct)),
               tarifa_nova='%s%% + R$ %s' % (pct, fixa), requer_liberacao=variacao > LIMITE_VARIACAO_PCT,
               mensagem='Subir de R$ %.2f para R$ %.2f (+%.1f%%) para chegar a %.1f%% de margem.' % (atual, novo, variacao, meta) +
                        (' Aumento acima de %s%%: exige liberação extra.' % LIMITE_VARIACAO_PCT if variacao > LIMITE_VARIACAO_PCT else ''))
    return reg


def reservar(item_id, modelo):
    agora = time.monotonic()
    with _LOCK:
        for k in [k for k, t in _RECENTES.items() if agora - t > JANELA_REAPLICACAO]:
            _RECENTES.pop(k, None)
        if (item_id, modelo) in _RECENTES:
            return False
        _RECENTES[(item_id, modelo)] = agora
        return True


def enviar_preco(item_id, modelo, novo):
    """update_price (original_price do modelo) e conferência. Devolve (ok, preço lido)."""
    r = chamar('/api/v2/product/update_price', corpo={'item_id': int(item_id), 'price_list': [{'model_id': int(modelo), 'original_price': float(novo)}]}) or {}
    falhas = [f for f in r.get('failure_list') or [] if str(f.get('model_id')) == str(modelo)]
    if falhas:
        raise ShopeeIndisponivel('A Shopee recusou: %s' % str(falhas[0].get('failed_reason') or '')[:160])
    _atual, original, _promo = modelo_atual(item_id, modelo)
    return abs(original - novo) < D('0.011'), _dois(original)


def validar_item_id(v):
    s = str(v or '').strip()
    if not s.isdigit() or not 5 <= len(s) <= 15:
        raise Bloqueio('Anúncio inválido')
    return s


def validar_modelo(v):
    s = str(v if v is not None else '').strip()
    if not s.isdigit() or len(s) > 15:
        raise Bloqueio('Variação inválida')
    return s
