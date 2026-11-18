"""Loja da Shopee dentro da Vendas do dia (conta SHOPEE): pedidos a enviar, lucro, preço, pausar e SKU. Sem Flask.

Fonte: API oficial da Shopee (Open Platform v2). O acesso da loja vem do App da Web "TokenSite" da planilha
Devoluções Marketplace (EXP_SHOPEE_TOKEN_URL, ação "token"): ela é a única dona do acesso e renova sozinha.
O site só lê o access_token; nunca renova.

Lucro de cada venda = líquido que a Shopee vai pagar (escrow_amount, já sem comissão, taxa de serviço, cupom da loja e
frete que ficou com você) − imposto (venda × %) − custo × quantidade. É o mesmo cálculo da aba Vendas do dia do ML:
tarifa = venda − líquido, frete = 0 (já dentro do líquido). Pedido com vários itens: o líquido é dividido entre os
itens pelo valor de cada um. Sem o líquido (a Shopee não respondeu), a linha fica "Sem dados" — nunca vira zero.

Preço necessário: comissão + taxa fixa por faixa de preço vêm da planilha (bloco "Taxas Shopee" da TaxasML, ação
shopee_taxas, a mesma do ML Hub); vale a MAIOR entre a tabela (+ campanha) e a média real da faixa (3+ itens).

Escritas na Shopee (sempre com confirmação na tela, relendo antes e conferindo depois): alterar preço (só sobe, sem
promoção, liberação acima de 15%), pausar anúncio (unlist) e trocar SKU (update_model / update_item).

Chave de cada linha: `mlb` = "<item_id>_<model_id>" (model 0 = sem variação), porque na Shopee cada variação tem preço
próprio; `anuncio` = item_id (pausar e SKU valem para o anúncio).
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
HOST_PADRAO = 'https://partner.shopeemobile.com'
MAX_APLICAR = 5
LIMITE_VARIACAO_PCT = D('15')
DIVERGENCIA_MAX_PCT = D('1')
PRECO_MIN, PRECO_MAX = D('1'), D('1000000')
JANELA_REAPLICACAO = 90
STATUS_A_ENVIAR = ('READY_TO_SHIP', 'PROCESSED')
STATUS_NOME = {'UNPAID': 'Não pago', 'READY_TO_SHIP': 'A enviar', 'PROCESSED': 'Etiqueta gerada', 'SHIPPED': 'Enviado', 'TO_CONFIRM_RECEIVE': 'Entregue',
               'COMPLETED': 'Concluído', 'IN_CANCEL': 'Cancelando', 'CANCELLED': 'Cancelado', 'TO_RETURN': 'Devolução', 'RETRY_SHIP': 'Reenvio', 'INVALID': 'Inválido'}
MIN_ITENS_REAL = 3
SKU_MAX = 100
_ORDER_SN = re.compile(r'^\d{6}[0-9A-Z]{8}$')      # ex.: 261104CDT9QASE (data + 8 letras/números)
_CHAVE = re.compile(r'^(\d{5,15})_(\d{1,15})$')
_SESS = requests.Session()
_LOCK = threading.Lock()
_TOKEN = {'valor': None, 'shop_id': None, 'ate': 0.0}
_TAXAS = {'dados': None, 'em': 0.0}
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
    """(access_token, shop_id) da loja, lido do TokenSite da planilha Devoluções (cache até 5 min antes de vencer)."""
    if not configurado():
        raise ShopeeIndisponivel('A Shopee ainda não está ligada no site (falta EXP_SHOPEE_PARTNER_ID, EXP_SHOPEE_PARTNER_KEY, EXP_SHOPEE_TOKEN_URL e EXP_SHOPEE_TOKEN_KEY no Render).')
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
def chave(item_id, modelo):
    return '%s_%s' % (item_id, modelo or 0)


def separar(ch):
    """'<item_id>_<model_id>' → (item_id, model_id). Inválida → Bloqueio."""
    m = _CHAVE.match(str(ch or '').strip())
    if not m:
        raise Bloqueio('Anúncio da Shopee inválido')
    return m.group(1), m.group(2)


def numero_valido(sn):
    return bool(_ORDER_SN.match(str(sn or '').strip().upper()))


# Etapa do envio da tela (a do Mercado Livre) → situações da Shopee. Na Shopee: "A enviar" = falta gerar a etiqueta
# (READY_TO_SHIP); "Etiqueta gerada" = pronta para despachar (PROCESSED). A Shopee não tem "aguardando liberação".
ETAPA_STATUS = {'todos': STATUS_A_ENVIAR, 'prontos_aguardando': ('PROCESSED',), 'prontos': ('PROCESSED',), 'impressa': ('PROCESSED',),
                'etiqueta': ('READY_TO_SHIP',), 'nf': ('READY_TO_SHIP',), 'aguardando': ()}


def pedidos_a_enviar(dias=30, status=STATUS_A_ENVIAR):
    """[(order_sn, status)] dos pedidos pagos ainda não enviados, criados nos últimos `dias`."""
    agora = int(time.time())
    saida, vistos = [], set()
    for st in status:
        fim = agora
        while fim > agora - dias * 86400:
            ini = max(agora - dias * 86400, fim - 15 * 86400)        # a Shopee aceita no máximo 15 dias por consulta
            cursor = ''
            for _ in range(50):
                r = chamar('/api/v2/order/get_order_list', {'time_range_field': 'create_time', 'time_from': ini, 'time_to': fim, 'page_size': 100,
                                                            'cursor': cursor, 'order_status': st, 'response_optional_fields': 'order_status'}) or {}
                for o in r.get('order_list') or []:
                    sn = str(o.get('order_sn') or '')
                    if sn and sn not in vistos:
                        vistos.add(sn)
                        saida.append((sn, str(o.get('order_status') or st)))
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
                                                     'response_optional_fields': 'item_list,total_amount,order_status,ship_by_date,create_time,fulfillment_flag'}) or {}
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
    return None if v is None else float(D(v).quantize(D('0.01')))


def _quando_dt(ts):
    try:
        return datetime.fromtimestamp(int(ts), FUSO) if ts else None
    except (TypeError, ValueError, OSError):
        return None


def linhas_dos_pedidos(pedidos, det, renda):
    """Uma linha por item, no formato da Vendas do dia (vendas.analisar): venda, tarifa (venda − líquido), frete 0."""
    saida = []
    for sn, status in pedidos:
        o = det.get(sn)
        if not o:
            continue
        status = str(o.get('order_status') or status)
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
        prazo = _quando_dt(o.get('ship_by_date'))
        full = str(o.get('fulfillment_flag') or '') == 'fulfilled_by_shopee'
        rotulo = STATUS_NOME.get(status, status) + (' · enviar até %s' % prazo.strftime('%d/%m %H:%M') if prazo and status in STATUS_A_ENVIAR else '')
        for it, v in zip(itens, valores):
            q = int(it.get('model_quantity_purchased') or 0)
            if q <= 0:
                continue
            parte = (v / total) if (v is not None and total) else None
            liq = liquido * parte if (liquido is not None and parte is not None) else None
            item_id, modelo = str(it.get('item_id') or ''), str(it.get('model_id') or 0)
            saida.append({
                'conta': CONTA, 'pedido': sn, 'pack_id': '', 'envio_id': '', 'grupo': 'full' if full else ('a_enviar' if status in STATUS_A_ENVIAR else 'enviado'),
                'logistica': 'shopee_full' if full else 'shopee', 'status_envio': status, 'substatus': '', 'status_rotulo': rotulo,
                'data': _quando(o.get('create_time')), 'limite_envio': prazo.strftime('%Y-%m-%d') if prazo else '', 'limite_dt': prazo.isoformat(timespec='seconds') if prazo else '',
                'mlb': chave(item_id, modelo), 'anuncio': item_id, 'sku': str(it.get('model_sku') or it.get('item_sku') or '').strip()[:80],
                'titulo': (str(it.get('item_name') or '') + (' — ' + str(it.get('model_name')) if it.get('model_name') else ''))[:200], 'quantidade': q,
                'venda': _dois(v), 'tarifa': _dois(v - liq) if (v is not None and liq is not None) else None, 'frete': 0.0 if liq is not None else None,
                'preco_ml': _dois(_n(it.get('model_original_price'))) if _n(it.get('model_original_price')) else None,
                'obs': 'Shopee: tarifa = venda − o que a Shopee vai pagar (comissão, serviço, cupom da loja e frete já descontados)' if liq is not None else 'A Shopee ainda não informou a renda deste pedido',
            })
    return saida


def _fora(linha, etapa, vence_ate, vence_apos, expira_ate):
    """Motivo pelo qual a linha fica fora do filtro da tela, ou '' (mesmos filtros do Mercado Livre)."""
    if linha['status_envio'] not in ETAPA_STATUS.get(etapa, STATUS_A_ENVIAR):
        return 'Fora da etapa escolhida (na Shopee está como "%s")' % STATUS_NOME.get(linha['status_envio'], linha['status_envio'])
    dia = linha['limite_envio']
    if dia and vence_ate and dia > vence_ate:
        return 'O prazo de envio é %s/%s (não vence hoje)' % (dia[8:10], dia[5:7])
    if dia and vence_apos and dia <= vence_apos:
        return 'O prazo de envio é hoje ou já passou'
    if linha['limite_dt'] and expira_ate is not None:
        try:
            if datetime.fromisoformat(linha['limite_dt']) > expira_ate:
                return 'O prazo de envio ainda não está na janela escolhida'
        except ValueError:
            pass
    return ''


def pedidos_vendas(etapa='todos', vence_ate=None, vence_apos=None, expira_ate=None, dias=30):
    """Pedidos a enviar da Shopee no formato da busca da Vendas do dia: {'itens', 'total', 'proximo', 'concluido'}."""
    status = ETAPA_STATUS.get(etapa, STATUS_A_ENVIAR)
    pedidos = pedidos_a_enviar(dias, status) if status else []
    sns = [sn for sn, _ in pedidos]
    det = detalhes(sns) if sns else {}
    renda = rendas(sns) if sns else {}
    itens = [l for l in linhas_dos_pedidos(pedidos, det, renda) if not _fora(l, etapa, vence_ate, vence_apos, expira_ate)]
    return {'itens': itens, 'total': len(pedidos), 'proximo': len(pedidos), 'concluido': True}


def pedidos_por_numero(sns):
    """Linhas dos pedidos da Shopee que o UpSeller mostra e a busca não trouxe (sem filtro de etapa). {'itens', 'ignorados'}."""
    det = detalhes(sns)
    ignorados, pedidos = [], []
    for sn in sns:
        o = det.get(sn)
        if not o:
            ignorados.append({'pedido': sn, 'motivo': 'Não encontrei este pedido na Shopee. Confira o número'})
        elif str(o.get('order_status') or '') in ('UNPAID', 'CANCELLED', 'IN_CANCEL', 'INVALID'):
            ignorados.append({'pedido': sn, 'motivo': 'Pedido está como "%s" na Shopee (não entra nas vendas)' % STATUS_NOME.get(o.get('order_status'), o.get('order_status'))})
        else:
            pedidos.append((sn, str(o.get('order_status') or '')))
    renda = rendas([p[0] for p in pedidos]) if pedidos else {}
    itens = linhas_dos_pedidos(pedidos, det, renda)
    for it in itens:
        it['pedido_upseller'] = it['pedido']
    return {'itens': itens, 'ignorados': ignorados}


def diagnosticar(sns, etapa='todos', vence_ate=None, vence_apos=None, expira_ate=None):
    """[{pedido, conta, situacao, motivo}] — por que estes pedidos da Shopee não apareceram na lista. Só leitura."""
    det = detalhes(sns)
    saida = []
    for sn in sns:
        o = det.get(sn)
        if not o:
            saida.append({'pedido': sn, 'conta': CONTA, 'situacao': '', 'motivo': 'Não encontrei este pedido na Shopee'})
            continue
        st = str(o.get('order_status') or '')
        if st not in STATUS_A_ENVIAR:
            saida.append({'pedido': sn, 'conta': CONTA, 'situacao': STATUS_NOME.get(st, st), 'motivo': 'Na Shopee está como "%s": não falta enviar' % STATUS_NOME.get(st, st)})
            continue
        linhas = linhas_dos_pedidos([(sn, st)], {sn: o}, {})
        motivo = _fora(linhas[0], etapa, vence_ate, vence_apos, expira_ate) if linhas else 'O pedido não tem itens'
        saida.append({'pedido': sn, 'conta': CONTA, 'situacao': STATUS_NOME.get(st, st), 'motivo': motivo})
    return saida


# ------------------------------------------------------------------ taxas e imposto (planilha, bloco "Taxas Shopee")
def taxas(validade=120):
    """{'imposto', 'extra', 'faixas': [{de, ate, pct, fixa, fixa_pct, real, itens}]} da planilha (ação shopee_taxas). Cache curto."""
    with _LOCK:
        if _TAXAS['dados'] is not None and time.monotonic() - _TAXAS['em'] < validade:
            return _TAXAS['dados']
    resp = fontes._sheets_post({'acao': 'shopee_taxas'}, espera=45, falta='A planilha ainda não tem a tabela de taxas da Shopee (Apps Script V31 ou mais nova).')
    faixas = []
    for f in resp.get('faixas') or []:
        f = list(f) + [0, None, 0][max(0, len(f) - 4):] if len(f) < 7 else list(f)
        de, ate, pct, fixa = _n(f[0]), (None if f[1] is None else _n(f[1])), _n(f[2]), _n(f[3])
        if de is None or pct is None or fixa is None or (f[1] is not None and ate is None):
            raise ShopeeIndisponivel('Faixa inválida na tabela de taxas da Shopee (planilha).')
        itens = int(f[6] or 0)
        real = None if f[5] is None else _n(f[5])
        faixas.append({'de': de, 'ate': ate, 'pct': pct, 'fixa': fixa, 'fixa_pct': _n(f[4]) or D(0), 'real': real if (real is not None and itens >= MIN_ITENS_REAL) else None, 'itens': itens})
    if not faixas:
        raise ShopeeIndisponivel('A tabela de taxas da Shopee (planilha) está vazia.')
    imposto = _n(resp.get('imposto_pct'))
    dados = {'imposto': imposto, 'extra': _n(resp.get('extra_pct')) or D(0), 'faixas': sorted(faixas, key=lambda x: x['de'])}
    with _LOCK:
        _TAXAS.update(dados=dados, em=time.monotonic())
    return dados


def imposto_pct():
    """{'normal': %, 'full': %} da Shopee (rótulo "Imposto Shopee" da TaxasML). Vazio = erro explicativo (nunca zero)."""
    imp = taxas()['imposto']
    if imp is None or not 0 <= imp < 60:
        raise ShopeeIndisponivel('O imposto da Shopee está vazio na planilha (rótulo "Imposto Shopee" da aba TaxasML).')
    return {'normal': float(imp), 'full': float(imp)}


def _faixa(preco, tx):
    for f in tx['faixas']:
        if f['de'] <= preco and (f['ate'] is None or preco < f['ate']):
            return f
    raise Bloqueio('Preço fora das faixas da tabela de taxas da Shopee')


def tarifa(preco, tx):
    """(taxa em R$, descrição): a MAIOR entre a tabela (+ campanha) e a média real da faixa."""
    f = _faixa(preco, tx)
    pct = f['pct'] + tx['extra'] + f['fixa_pct']
    tabela = preco * pct / 100 + f['fixa']
    desc = '%s%% + R$ %s' % (pct.normalize(), f['fixa'].normalize())
    if f['real'] is not None and preco * f['real'] / 100 > tabela:
        return (preco * f['real'] / 100).quantize(D('0.01')), 'média real %s%%' % f['real'].normalize()
    return tabela.quantize(D('0.01')), desc


def margem_no_preco(custo, preco, imposto, tx):
    t, _ = tarifa(preco, tx)
    return (preco - t - preco * imposto / 100 - custo) / custo * 100


def preco_alvo(custo, meta, imposto, tx):
    """Menor preço (centavos, para cima) que atinge a margem com a taxa da faixa desse preço."""
    if custo is None or custo <= 0 or meta is None or meta < 0 or imposto is None:
        raise Bloqueio('Falta custo, margem ou imposto')
    alvo = custo * (1 + meta / 100)
    melhores = []
    for f in tx['faixas']:
        fator = 1 - (f['pct'] + tx['extra'] + f['fixa_pct']) / 100 - imposto / 100
        if fator <= 0:
            continue
        p = max(f['de'], (alvo + f['fixa']) / fator)
        if f['real'] is not None:
            fr = 1 - f['real'] / 100 - imposto / 100
            if fr <= 0:
                continue
            p = max(p, alvo / fr)
        p = p.quantize(D('0.01'), rounding=ROUND_CEILING)
        if (f['ate'] is None or p < f['ate']) and margem_no_preco(custo, p, imposto, tx) >= meta - D('0.005'):
            melhores.append(p)
    if not melhores:
        raise Bloqueio('Não achei um preço que atinja a margem com as faixas da tabela de taxas da Shopee')
    return min(melhores)


# ------------------------------------------------------------------ anúncios
def _nome_modelo(m, tiers):
    partes = []
    for i, idx in enumerate(m.get('tier_index') or []):
        try:
            t = tiers[i]
            partes.append('%s: %s' % (t.get('name') or '', (t.get('option_list') or [])[idx].get('option') or ''))
        except (IndexError, AttributeError, TypeError):
            continue
    return ' / '.join(partes)[:80]


def anuncio(item_id):
    """{'item': base, 'modelos': [...], 'tiers': [...]} do anúncio (get_item_base_info + get_model_list)."""
    base = chamar('/api/v2/product/get_item_base_info', {'item_id_list': str(item_id)}) or {}
    it = next((x for x in base.get('item_list') or [] if str(x.get('item_id')) == str(item_id)), None)
    if not it:
        raise Bloqueio('A Shopee não retornou este anúncio')
    r = chamar('/api/v2/product/get_model_list', {'item_id': int(item_id)}) or {}
    return {'item': it, 'modelos': [m for m in r.get('model') or [] if isinstance(m, dict)], 'tiers': r.get('tier_variation') or []}


def locais(d):
    """[(model_id, nome, sku, price_info, em promoção)]: um por variação; sem variação, o próprio anúncio ('0')."""
    it = d['item']
    if d['modelos']:
        return [(str(m.get('model_id')), _nome_modelo(m, d['tiers']), str(m.get('model_sku') or '').strip(), (m.get('price_info') or [None])[0],
                 bool(m.get('promotion_id') or m.get('has_promotion'))) for m in d['modelos'] if str(m.get('model_id') or '').isdigit()]
    return [('0', '', str(it.get('item_sku') or '').strip(), (it.get('price_info') or [None])[0], bool(it.get('promotion_id')))]


def modelo_atual(item_id, modelo):
    """(preço atual, preço original, em promoção) do modelo ('0' = anúncio sem variação)."""
    d = anuncio(item_id)
    if str(modelo) == '0' and d['modelos']:
        raise Bloqueio('O anúncio tem variações; não identifiquei qual foi vendida')
    loc = next((x for x in locais(d) if x[0] == str(modelo)), None)
    if loc is None:
        raise Bloqueio('A variação vendida não existe mais no anúncio')
    info = loc[3] or {}
    atual, original = _n(info.get('current_price')), _n(info.get('original_price'))
    if atual is None or original is None or atual <= 0:
        raise Bloqueio('A Shopee não informou o preço do anúncio')
    return atual, original, loc[4] or atual != original


def analisar_preco(ch, custo, meta, imposto):
    """Preço necessário para o anúncio (chave item_modelo) chegar na margem. acao = 'subir' | 'ok'."""
    item_id, modelo = separar(ch)
    tx = taxas()
    atual, original, promo = modelo_atual(item_id, modelo)
    reg = {'mlb': ch, 'preco_atual': _dois(atual), 'custo': _dois(custo), 'meta': _dois(meta), 'margem_atual': _dois(margem_no_preco(custo, atual, imposto, tx)) if custo else None,
           'acao': 'ok', 'preco_novo': None, 'variacao_pct': None, 'margem_nova': None, 'requer_liberacao': False}
    novo = preco_alvo(custo, meta, imposto, tx)
    if novo <= atual:
        reg['mensagem'] = 'O preço atual do anúncio na Shopee já atinge a margem (a venda pode ter sido com desconto ou cupom).'
        return reg
    if promo:
        raise Bloqueio('Anúncio em promoção na Shopee: altere o preço lá (a promoção usa outro preço)')
    variacao = (novo / atual - 1) * 100
    _t, desc = tarifa(novo, tx)
    reg.update(acao='subir', preco_novo=_dois(novo), variacao_pct=_dois(variacao), margem_nova=_dois(margem_no_preco(custo, novo, imposto, tx)), tarifa_nova=desc,
               requer_liberacao=variacao > LIMITE_VARIACAO_PCT,
               mensagem='Subir na Shopee de R$ %.2f para R$ %.2f (+%.1f%%, taxa %s) para chegar a %.1f%% de margem.' % (atual, novo, variacao, desc, meta) +
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


def aplicar_preco(ch, custo, meta, imposto, visto_atual, visto_novo, liberar):
    """Altera o preço de uma variação (relê e recalcula na hora). {'mlb','status','mensagem',...}. Nunca levanta."""
    r = {'mlb': ch, 'status': 'PULADO', 'mensagem': ''}
    try:
        item_id, modelo = separar(ch)
        a = analisar_preco(ch, custo, meta, imposto)
        if a['acao'] != 'subir':
            raise Bloqueio('Não precisa mais de ajuste: ' + a.get('mensagem', ''))
        atual, novo = D(str(a['preco_atual'])), D(str(a['preco_novo']))
        if abs(atual - visto_atual) > D('0.009'):
            raise Bloqueio('O preço mudou na Shopee desde que você viu. Calcule de novo.')
        if abs(novo / visto_novo - 1) * 100 > DIVERGENCIA_MAX_PCT:
            raise Bloqueio('Custo, taxa ou margem mudaram desde que você viu. Calcule de novo.')
        if a['requer_liberacao'] and not liberar:
            raise Bloqueio('Aumento acima de %s%% exige a liberação extra.' % LIMITE_VARIACAO_PCT)
        if not reservar(item_id, modelo):
            raise Bloqueio('Este anúncio acabou de ser alterado. Aguarde um minuto.')
        ok, lido = enviar_preco(item_id, modelo, novo)
        r.update(status='OK' if ok else 'PENDENTE', preco_anterior=a['preco_atual'], preco_novo=a['preco_novo'], verificado=lido,
                 mensagem='Preço alterado e confirmado na Shopee.' if ok else 'Enviado, mas a Shopee ainda não confirmou. Confira no anúncio.')
    except Bloqueio as exc:
        r['mensagem'] = str(exc)
    except Exception as exc:
        r.update(status='ERRO', mensagem=str(exc)[:200])
    return r


def validar_item_id(v):
    s = str(v or '').strip()
    if not s.isdigit() or not 5 <= len(s) <= 15:
        raise Bloqueio('Anúncio da Shopee inválido')
    return s


def pausar_item(item_id):
    """Tira o anúncio do ar na Shopee (unlist), só se estiver ativo, e confere. Nunca levanta."""
    r = {'mlb': str(item_id), 'status': 'PULADO', 'mensagem': ''}
    try:
        d = anuncio(item_id)
        st = str(d['item'].get('item_status') or '')
        if st != 'NORMAL':
            raise Bloqueio('Anúncio não está ativo na Shopee (situação: %s)' % (st or 'desconhecida'))
        resp = chamar('/api/v2/product/unlist_item', corpo={'item_list': [{'item_id': int(item_id), 'unlist': True}]}) or {}
        falhas = [f for f in resp.get('failure_list') or [] if str(f.get('item_id')) == str(item_id)]
        if falhas:
            raise ShopeeIndisponivel('A Shopee recusou: %s' % str(falhas[0].get('failed_reason') or '')[:160])
        depois = str(anuncio(item_id)['item'].get('item_status') or '')
        if depois == 'UNLIST':
            r.update(status='OK', mensagem='Anúncio pausado (fora do ar) na Shopee.')
        else:
            r.update(status='NAO_CONFIRMADO', mensagem='Enviado, mas a Shopee ainda mostra "%s". Confira.' % depois)
    except Bloqueio as exc:
        r['mensagem'] = str(exc)
    except Exception as exc:
        r.update(status='ERRO', mensagem=str(exc)[:200])
    return r


def skus_do_anuncio(item_id):
    """{'mlb', 'titulo', 'locais': [{variacao, nome, sku}]} — SKU de cada variação (somente leitura)."""
    d = anuncio(item_id)
    return {'mlb': str(item_id), 'titulo': str(d['item'].get('item_name') or '')[:120],
            'locais': [{'variacao': mid, 'nome': nome, 'sku': sku} for mid, nome, sku, _p, _pr in locais(d)]}


def trocar_sku_item(item_id, trocas):
    """Troca o SKU de variações do anúncio (só se o SKU de hoje for o visto) e confere. Nunca levanta."""
    r = {'mlb': str(item_id), 'status': 'PULADO', 'mensagem': '', 'trocas': []}
    try:
        if not isinstance(trocas, list) or not 1 <= len(trocas) <= 50:
            raise Bloqueio('Nenhuma troca informada')
        d = anuncio(item_id)
        hoje = {mid: sku for mid, _n2, sku, _p, _pr in locais(d)}
        feitas = []
        for t in trocas:
            mid = str((t or {}).get('variacao') or '0').strip() or '0'
            novo, visto = str(t.get('sku_novo') or '').strip(), str(t.get('sku_atual') or '').strip()
            if mid not in hoje:
                raise Bloqueio('A variação %s não existe mais no anúncio' % mid)
            if not novo or len(novo) > SKU_MAX or any(c in novo for c in '\r\n\t'):
                raise Bloqueio('SKU novo inválido')
            if hoje[mid] != visto:
                raise Bloqueio('O SKU mudou na Shopee desde a lista (agora: %s)' % (hoje[mid] or 'vazio'))
            feitas.append((mid, novo))
        for mid, novo in feitas:
            if mid == '0':
                chamar('/api/v2/product/update_item', corpo={'item_id': int(item_id), 'item_sku': novo})
            else:
                chamar('/api/v2/product/update_model', corpo={'item_id': int(item_id), 'model': [{'model_id': int(mid), 'model_sku': novo}]})
        lido = {mid: sku for mid, _n2, sku, _p, _pr in locais(anuncio(item_id))}
        if set(lido) != set(hoje):
            r.update(status='NAO_CONFIRMADO', mensagem='ATENÇÃO: as variações do anúncio mudaram depois da alteração. Confira na Shopee.')
        elif all(lido.get(mid) == novo for mid, novo in feitas):
            r.update(status='OK', mensagem='SKU alterado e confirmado na Shopee.', trocas=[{'variacao': m, 'sku_novo': n} for m, n in feitas])
        else:
            r.update(status='NAO_CONFIRMADO', mensagem='Enviado, mas a Shopee ainda não mostra o SKU novo. Confira.')
    except Bloqueio as exc:
        r['mensagem'] = str(exc)
    except Exception as exc:
        r.update(status='ERRO', mensagem=str(exc)[:200])
    return r
