"""Fontes de dados do painel: custo (planilha de custos via Apps Script) e frete (API do Mercado Livre).

Tudo é somente leitura. Falha de consulta NUNCA vira zero: devolve None + motivo.

Variáveis de ambiente:
  EXP_SHEETS_URL, EXP_SHEETS_KEY   -> Apps Script da planilha de custos (aba DadosBling, coluna Custo)
  EXP_TOKEN_URL_ML19, EXP_TOKEN_URL_ML44 -> Apps Scripts que devolvem o access_token de cada conta
"""
import os
import re
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter

API = 'https://api.mercadolibre.com'
CONTAS = ('ML19', 'ML44')
TTL_TOKEN = 240
TTL_FRETE = 6 * 3600
MAX_MLBS_POR_PRODUTO = 3          # tenta até 3 anúncios do produto até conseguir cotar
LOTE_CUSTOS = 50

_SESS = requests.Session()
_SESS.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=16))
_LOCK = threading.Lock()
_TOKENS, _VENDEDOR, _FRETES = {}, {}, {}


class FonteIndisponivel(RuntimeError):
    """Mensagem pensada para aparecer na tela."""


# ---------------------------------------------------------------- custo
def _numero(v):
    if v is None or isinstance(v, bool):
        return None
    t = str(v).replace('R$', '').replace('\xa0', '').replace(' ', '')
    if not t:
        return None
    if ',' in t and '.' in t:
        t = t.replace('.', '').replace(',', '.') if t.rfind(',') > t.rfind('.') else t.replace(',', '')
    elif ',' in t:
        t = t.replace(',', '.')
    try:
        n = float(t)
    except ValueError:
        return None
    return n if n == n and abs(n) != float('inf') else None


def _consultar_itens(url, chave, lista):
    """{sku_pedido: {custo, margem, margem_full, ...}} (valores crus da planilha) para uma lista de SKUs, em lotes."""
    saida = {}
    for i in range(0, len(lista), LOTE_CUSTOS):
        lote = lista[i:i + LOTE_CUSTOS]
        try:
            r = _SESS.post(url, json={'chave': chave, 'acao': 'catalogo_custos', 'skus': lote}, timeout=(5, 25), allow_redirects=True)
            r.raise_for_status()
            dados = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise FonteIndisponivel('Não consegui ler a planilha de custos agora (%s). Tente de novo em instantes.' % str(exc)[:80])
        if not isinstance(dados, dict) or dados.get('ok') is not True or not isinstance(dados.get('items'), dict):
            raise FonteIndisponivel('A planilha de custos respondeu de um jeito inesperado. Confira o Apps Script.')
        for sku, valor in dados['items'].items():
            if isinstance(valor, dict):
                saida[sku] = valor
    return saida


def _consultar_custos(url, chave, lista):
    """{sku_pedido: custo_bruto} para uma lista de SKUs (em lotes)."""
    return {sku: _numero(v.get('custo')) for sku, v in _consultar_itens(url, chave, lista).items()}


def _percentual(v):
    """Margem da planilha ('20%', '20,5 %', 0.2 ou 20) em pontos percentuais. Vazio ou inválido = None."""
    if v is None or isinstance(v, bool):
        return None
    txt = str(v).strip()
    n = _numero(txt.replace('%', ''))
    if n is None or n < 0 or n >= 1000:
        return None
    return n if '%' in txt or n >= 1 else n * 100        # 0,2 (fração) vira 20; 20 e 20% ficam 20

def _variacoes(sku):
    """Grafias parecidas (ex.: _5kg no ML e _5Kg no Bling)."""
    v = {sku.upper(), sku.lower(), sku.title(), sku.swapcase()}
    if sku.lower().endswith('kg'):
        raiz = sku[:-2]
        v.update(raiz + u for u in ('kg', 'Kg', 'KG', 'kG'))
    v.discard(sku)
    return v


def custos(skus):
    """{sku: custo|None}. Custo ausente ou <= 0 vira None (nunca zero).

    Se o SKU não existe na planilha, tenta variações de maiúsculas/minúsculas; só aceita se houver
    uma única correspondência (mais de uma seria palpite e fica sem custo).
    """
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        raise FonteIndisponivel('A planilha de custos não está configurada (EXP_SHEETS_URL e EXP_SHEETS_KEY no Render).')
    lista = sorted({str(s).strip() for s in skus if str(s or '').strip()})
    achados = _consultar_custos(url, chave, lista)
    saida = {}
    for sku in lista:
        c = achados.get(sku)
        saida[sku] = c if c is not None and c > 0 else None
    faltam = [s for s in lista if s not in achados]
    if faltam:
        candidatos = set()
        for sku in faltam:
            candidatos |= _variacoes(sku)
        candidatos -= set(lista)
        alt = _consultar_custos(url, chave, sorted(candidatos)) if candidatos else {}
        for sku in faltam:
            certos = [c for k, c in alt.items() if k.casefold() == sku.casefold()]
            if len(certos) == 1 and certos[0] is not None and certos[0] > 0:
                saida[sku] = certos[0]
    return saida


def cadastro(skus):
    """{sku: {'custo', 'margem', 'margem_full'}} da planilha (custo em R$, margens em %). Ausente = None, nunca zero.

    Mesma busca de custos(): se o SKU não existe, tenta grafias parecidas e só aceita uma única correspondência.
    """
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        raise FonteIndisponivel('A planilha de custos não está configurada (EXP_SHEETS_URL e EXP_SHEETS_KEY no Render).')
    lista = sorted({str(s).strip() for s in skus if str(s or '').strip()})
    achados = _consultar_itens(url, chave, lista)
    faltam = [s for s in lista if s not in achados]
    if faltam:
        candidatos = set()
        for sku in faltam:
            candidatos |= _variacoes(sku)
        candidatos -= set(lista)
        alt = _consultar_itens(url, chave, sorted(candidatos)) if candidatos else {}
        for sku in faltam:
            certos = [v for k, v in alt.items() if k.casefold() == sku.casefold()]
            if len(certos) == 1:
                achados[sku] = certos[0]
    saida = {}
    for sku in lista:
        v = achados.get(sku) or {}
        c = _numero(v.get('custo'))
        saida[sku] = {'custo': c if c is not None and c > 0 else None, 'margem': _percentual(v.get('margem')), 'margem_full': _percentual(v.get('margem_full'))}
    return saida


def aptos_full():
    """SKUs da aba 'Aptos Para FULL' da planilha (via Apps Script, ação 'aptos_full'), em minúsculas e sem espaços nas pontas."""
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        raise FonteIndisponivel('A planilha de custos não está configurada (EXP_SHEETS_URL e EXP_SHEETS_KEY no Render).')
    try:
        r = _SESS.post(url, json={'chave': chave, 'acao': 'aptos_full'}, timeout=(5, 25), allow_redirects=True)
        r.raise_for_status()
        dados = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise FonteIndisponivel('Não consegui ler a aba Aptos Para FULL agora (%s).' % str(exc)[:80])
    if not isinstance(dados, dict) or dados.get('ok') is not True or not isinstance(dados.get('skus'), list):
        raise FonteIndisponivel('A planilha ainda não entrega a lista da aba Aptos Para FULL. Falta colocar o trecho novo no Apps Script (peça o passo a passo).')
    return sorted({str(x).strip().lower() for x in dados['skus'] if str(x or '').strip()})


# ---------------------------------------------------------------- frete
_TOKEN_OK = re.compile(r'^[A-Za-z0-9._~+/=-]{10,600}$')


def token(conta):
    if conta not in CONTAS:
        raise FonteIndisponivel('Conta inválida.')
    with _LOCK:
        achado = _TOKENS.get(conta)
        if achado and time.monotonic() - achado[1] < TTL_TOKEN:
            return achado[0]
    url = os.getenv('EXP_TOKEN_URL_' + conta, '').strip()
    if not url.startswith('https://script.google.com/'):
        raise FonteIndisponivel('O acesso ao Mercado Livre da conta %s não está configurado (EXP_TOKEN_URL_%s no Render).' % (conta, conta))
    valor = ''
    for tentativa in range(2):      # o Apps Script às vezes devolve uma página de erro uma vez e acerta na seguinte
        try:
            r = _SESS.get(url, timeout=(5, 30), allow_redirects=True)
            r.raise_for_status()
            try:
                dados = r.json()
            except ValueError:
                dados = r.text.strip()
        except requests.RequestException as exc:
            if tentativa == 0:
                time.sleep(1.5)
                continue
            raise FonteIndisponivel('Não consegui o acesso ao Mercado Livre agora (%s).' % str(exc)[:80])
        valor = (dados.get('access_token') or dados.get('accessToken') or dados.get('token')) if isinstance(dados, dict) else str(dados).strip().strip('"')
        valor = str(valor or '').strip()
        if _TOKEN_OK.match(valor):
            break
        valor = ''
        if tentativa == 0:
            time.sleep(1.5)
    if not valor:
        raise FonteIndisponivel('O Google (Apps Script) não devolveu o acesso da conta %s: veio uma página de erro em vez do código. Tente de novo em instantes; se repetir, abra a implantação do Apps Script dessa conta e confira se está como "Qualquer pessoa" e sem erro.' % conta)
    with _LOCK:
        _TOKENS[conta] = (str(valor).strip(), time.monotonic())
    return _TOKENS[conta][0]


def _ml(tk, caminho, params=None):
    ultimo = None
    for tentativa in range(3):
        try:
            r = _SESS.get(API + caminho, params=params, headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 18))
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError('HTTP %s' % r.status_code)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            ultimo = exc
            if tentativa < 2:
                time.sleep(0.6 + tentativa)
    raise RuntimeError(str(ultimo))


def _vendedor(conta, tk):
    with _LOCK:
        if conta in _VENDEDOR:
            return _VENDEDOR[conta]
    uid = str((_ml(tk, '/users/me') or {}).get('id') or '')
    if not uid.isdigit():
        raise FonteIndisponivel('Não identifiquei o vendedor da conta %s.' % conta)
    with _LOCK:
        _VENDEDOR[conta] = uid
    return uid


def frete_mlb(tk, vendedor, mlb):
    """Custo de frete do vendedor (list_cost da cotação V3) de um anúncio. (valor|None, motivo)."""
    try:
        item = _ml(tk, '/items/' + mlb)
        if not isinstance(item, dict) or item.get('id') != mlb:
            return None, 'Anúncio não identificado'
        # A cotação não depende de estoque: anúncio pausado ou sem estoque também é cotado
        # (nada é alterado no Mercado Livre). Encerrado ou em revisão não cota.
        if item.get('status') not in ('active', 'paused', 'inactive'):
            return None, 'Anúncio %s (encerrado ou em revisão)' % item.get('status')
        sh = item.get('shipping') or {}
        if sh.get('mode') not in ('me1', 'me2'):
            return None, 'Modalidade de envio não suportada'
        if str(item.get('seller_id') or '') != vendedor:
            return None, 'Anúncio não pertence à conta escolhida'
        try:
            preco = float(item.get('price'))
        except (TypeError, ValueError):
            return None, 'Preço do anúncio inválido'
        if not preco > 0:
            return None, 'Preço do anúncio inválido'
        if not isinstance(sh.get('free_shipping'), bool):
            return None, 'Frete grátis não identificado'
        params = {'item_id': mlb, 'item_price': str(preco), 'listing_type_id': item.get('listing_type_id'), 'mode': sh.get('mode'),
                  'logistic_type': sh.get('logistic_type'), 'free_shipping': str(sh['free_shipping']).lower(), 'verbose': 'true'}
        dados = _ml(tk, '/users/%s/shipping_options/free' % vendedor, {k: v for k, v in params.items() if v})
        pais = ((dados.get('coverage') or {}).get('all_country') or {}) if isinstance(dados, dict) else {}
        if not isinstance(pais, dict) or pais.get('currency_id') != 'BRL':
            return None, 'Cobertura de frete inválida'
        valor = _numero(pais.get('list_cost'))
        if valor is None or valor < 0:
            return None, 'Custo do frete não retornado'
        return valor, 'Cotado'
    except Exception as exc:
        return None, 'Erro na consulta: ' + str(exc)[:70]


def frete_produto(conta, tk, vendedor, mlbs):
    """Tenta os anúncios do produto, em ordem, até cotar. Devolve {'frete', 'erro', 'mlb'}."""
    motivos = []
    for mlb in (mlbs or [])[:MAX_MLBS_POR_PRODUTO]:
        with _LOCK:
            c = _FRETES.get((conta, mlb))
        if c and time.monotonic() - c[1] < TTL_FRETE:
            return {'frete': c[0], 'erro': '', 'mlb': mlb}
        valor, motivo = frete_mlb(tk, vendedor, mlb)
        if valor is not None:
            with _LOCK:
                _FRETES[(conta, mlb)] = (valor, time.monotonic())
            return {'frete': valor, 'erro': '', 'mlb': mlb}
        motivos.append('%s: %s' % (mlb, motivo))
    return {'frete': None, 'erro': '; '.join(motivos) or 'Produto sem anúncio para cotar', 'mlb': ''}


def fretes(conta, itens, espera_max=24):
    """{codigo_ml: {'frete','erro','mlb'}} para uma lista pequena de produtos. Respeita um limite de tempo."""
    tk = token(conta)
    vendedor = _vendedor(conta, tk)
    inicio = time.monotonic()
    ex = ThreadPoolExecutor(max_workers=6)
    futuros = {i['codigo_ml']: ex.submit(frete_produto, conta, tk, vendedor, i.get('mlbs')) for i in itens}
    saida, pendentes = {}, []
    for cod, fut in futuros.items():
        restante = max(0.1, espera_max - (time.monotonic() - inicio))
        try:
            saida[cod] = fut.result(timeout=restante)
        except Exception:
            pendentes.append(cod)
    ex.shutdown(wait=False)
    return saida, pendentes


# ---------------------------------------------------------------- pedidos (candidatos ao Full)
import re as _re
_MLB = _re.compile(r'^MLB\d{6,14}$')
LOGISTICA = {'fulfillment': 'Full', 'self_service': 'Flex', 'drop_off': 'Agência/coleta', 'xd_drop_off': 'Agência/coleta',
             'cross_docking': 'Coleta', 'default': 'Envio padrão'}


def pedidos_pagina(conta, inicio, fim, offset=0):
    """Uma página (até 50 pedidos pagos) do período [inicio, fim] (AAAA-MM-DD, horário de Brasília).

    Devolve {'itens': [{mlb, titulo, sku, unidades, pedidos, valor}], 'total', 'proximo', 'concluido'}.
    Só leitura. Pedidos cancelados não entram.
    """
    tk = token(conta)
    vendedor = _vendedor(conta, tk)
    params = {'seller': vendedor, 'order.date_created.from': inicio + 'T00:00:00.000-03:00', 'order.date_created.to': fim + 'T23:59:59.999-03:00',
              'order.status': 'paid', 'sort': 'date_asc', 'limit': 50, 'offset': int(offset)}
    try:
        dados = _ml(tk, '/orders/search', params)
    except RuntimeError as exc:
        raise FonteIndisponivel('Não consegui ler os pedidos do Mercado Livre agora (%s). Tente de novo.' % str(exc)[:80])
    if not isinstance(dados, dict) or not isinstance(dados.get('results'), list):
        raise FonteIndisponivel('O Mercado Livre respondeu os pedidos de um jeito inesperado.')
    agreg = {}
    for ped in dados['results']:
        if not isinstance(ped, dict) or str(ped.get('status') or '') in ('cancelled', 'invalid'):
            continue
        for oi in ped.get('order_items') or []:
            it = (oi or {}).get('item') or {}
            mlb = str(it.get('id') or '')
            if not _MLB.match(mlb):
                continue
            try:
                qtd = int(oi.get('quantity') or 0)
            except (TypeError, ValueError):
                qtd = 0
            preco = _numero(oi.get('unit_price')) or 0.0
            a = agreg.setdefault(mlb, {'mlb': mlb, 'titulo': str(it.get('title') or '')[:200], 'sku': str(it.get('seller_sku') or it.get('seller_custom_field') or '').strip()[:80],
                                       'unidades': 0, 'ped': set(), 'valor': 0.0})
            a['unidades'] += max(0, qtd)
            a['valor'] += max(0, qtd) * preco
            a['ped'].add(ped.get('id'))
            if not a['sku']:
                a['sku'] = str(it.get('seller_sku') or it.get('seller_custom_field') or '').strip()[:80]
    itens = [{'mlb': a['mlb'], 'titulo': a['titulo'], 'sku': a['sku'], 'unidades': a['unidades'], 'pedidos': len(a['ped']), 'valor': round(a['valor'], 2)} for a in agreg.values()]
    pag = dados.get('paging') or {}
    total = int(pag.get('total') or 0)
    proximo = int(offset) + len(dados['results'])
    return {'itens': itens, 'total': total, 'proximo': proximo, 'concluido': not dados['results'] or proximo >= total}


def itens_info(conta, ids):
    """{mlb: {status, preco, estoque, logistica, rotulo, sku, link}} para até 20 anúncios."""
    ids = [i for i in ids if _MLB.match(str(i))][:20]
    if not ids:
        return {}
    tk = token(conta)
    try:
        dados = _ml(tk, '/items', {'ids': ','.join(ids), 'attributes': 'id,status,price,available_quantity,seller_custom_field,shipping,permalink'})
    except RuntimeError as exc:
        raise FonteIndisponivel('Não consegui ler os anúncios do Mercado Livre agora (%s). Tente de novo.' % str(exc)[:80])
    saida = {}
    for x in dados if isinstance(dados, list) else []:
        corpo = (x or {}).get('body') if isinstance(x, dict) else None
        if not isinstance(corpo, dict) or x.get('code') != 200 or not _MLB.match(str(corpo.get('id') or '')):
            continue
        log = str(((corpo.get('shipping') or {}).get('logistic_type')) or '')
        saida[corpo['id']] = {'status': str(corpo.get('status') or ''), 'preco': _numero(corpo.get('price')), 'estoque': corpo.get('available_quantity') if isinstance(corpo.get('available_quantity'), int) else None,
                              'logistica': log, 'rotulo': LOGISTICA.get(log, log or '—'), 'sku': str(corpo.get('seller_custom_field') or '').strip()[:80],
                              'link': str(corpo.get('permalink') or '') if str(corpo.get('permalink') or '').startswith('https://') else ''}
    return saida


# ---------------------------------------------------------------- vendas com tarifa e frete (aba Vendas do dia)
# etapas do envio que o Mercado Livre mostra. Conferido contra os números do UpSeller em 03/11: "Pronto para coleta" (ML) ≈ Para Enviar/Para Imprimir (UpSeller);
# "Gerar NF" ≈ Para Emitir; "Aguardando (buffered)" fica de fora das etapas (o UpSeller não o põe em Enviar/Imprimir)
ETAPAS_ENVIO = {'todos': None, 'nf': {'invoice_pending'}, 'etiqueta': {'ready_to_print', 'ready_for_pickup', 'ready_for_dispatch', 'waiting_for_pickup'},
                'impressa': {'printed'},
                'prontos': {'ready_to_print', 'ready_for_pickup', 'ready_for_dispatch', 'waiting_for_pickup', 'printed'},   # etiqueta liberada OU já impressa: tudo que ainda não foi bipado na coleta
                'aguardando': {'buffered'},                                                                                    # o ML adiou a data de envio ("Para Reservar" / Pendente Revisão do UpSeller): a etiqueta ainda vai liberar
                'prontos_aguardando': {'ready_to_print', 'ready_for_pickup', 'ready_for_dispatch', 'waiting_for_pickup', 'printed', 'buffered'}}   # como o Modo Rápido do UpSeller: prontos + os que ainda vão liberar
SUBS_NA_COLETA = {'printed', 'ready_for_pickup', 'ready_for_dispatch', 'waiting_for_pickup'}     # pacote pronto, esperando a coleta
FINAIS_ENVIO = ('shipped', 'in_transit', 'out_for_delivery', 'delivered', 'not_delivered', 'returned', 'cancelled', 'canceled')


def _frete_vendedor(dados, vendedor):
    """Frete debitado do vendedor no envio (nunca o pago pelo comprador). None se a resposta não traz o valor."""
    if not isinstance(dados, dict):
        return None
    for rem in dados.get('senders') or []:
        if not isinstance(rem, dict):
            continue
        uid = rem.get('user_id') or rem.get('id')
        if uid and str(uid) != str(vendedor):
            continue
        for campo in ('cost', 'shipping_cost'):
            v = _numero(rem.get(campo))
            if v is not None:
                return abs(v)
    for campo in ('seller_cost', 'seller_shipping_cost'):
        v = _numero(dados.get(campo))
        if v is not None:
            return abs(v)
    return None


def rotulo_envio(status, substatus=''):
    """Situação do envio em palavras da expedição (Gerar NF, Gerar etiqueta…). Desconhecido aparece como veio do ML, sem inventar."""
    st, sub = str(status or '').lower(), str(substatus or '').lower()
    if st == 'delivered':
        return 'Entregue'
    if st in ('shipped', 'in_transit', 'out_for_delivery'):
        return 'Enviado'
    if st in ('cancelled', 'canceled'):
        return 'Cancelado'
    if st in ('not_delivered', 'returned'):
        return 'Não entregue / devolvido'
    if st == 'ready_to_ship':
        if sub == 'invoice_pending':
            return 'Gerar NF'
        if sub == 'ready_to_print':
            return 'Gerar etiqueta'
        if sub == 'printed':
            return 'Etiqueta impressa'
        if sub in ('ready_for_pickup', 'ready_for_dispatch', 'waiting_for_pickup', 'picked_up', 'dropped_off'):
            return 'Pronto para coleta'
        return 'Pronto para envio' + (' (%s)' % sub if sub else '')
    if st == 'handling':
        return 'Preparando' + (' (%s)' % sub if sub else '')
    if st == 'pending':
        return 'Aguardando' + (' (%s)' % sub if sub else '')
    return (st + (' / ' + sub if sub else '')) if st else 'Não consultado'


def _grupo_envio(logistica, status):
    if logistica == 'fulfillment':
        return 'full'
    return 'enviado' if str(status or '').lower() in FINAIS_ENVIO else 'a_enviar'


def _envio_do_pedido(tk, vendedor, ped, somente_a_enviar=False, etapa='todos', vence_ate=None, vence_apos=None, expira_ate=None):
    """{'frete', 'logistica', 'status_envio', 'divisor', 'erro'} do envio de um pedido."""
    env = (ped.get('shipping') or {}).get('id')
    if not env and ped.get('id'):                  # a busca às vezes vem sem o envio: confere no pedido completo antes de desistir
        try:
            env = ((_ml(tk, '/orders/%s' % ped['id']) or {}).get('shipping') or {}).get('id')
        except (RuntimeError, ValueError, AttributeError):
            env = None
    saida = {'frete': None, 'logistica': '', 'status_envio': '', 'substatus': '', 'divisor': 1, 'erro': '', 'limite': '', 'limite_dt': '', 'envio_id': str(env or '')}
    if not env:
        saida['erro'] = 'Pedido sem envio'
        return saida
    try:
        sh = _ml(tk, '/shipments/%s' % env)
        if isinstance(sh, dict):
            saida['status_envio'] = str(sh.get('status') or '')
            saida['substatus'] = str(sh.get('substatus') or '')
            saida['logistica'] = str(sh.get('logistic_type') or (sh.get('logistic') or {}).get('type') or '')
            lim = ((sh.get('lead_time') or {}).get('estimated_handling_limit') or {}).get('date')     # prazo para despachar (o "Expira em" do UpSeller)
            saida['limite'] = str(lim or '')[:10] if _re.match(r'^\d{4}-\d{2}-\d{2}', str(lim or '')) else ''
            saida['limite_dt'] = str(lim)[:29] if saida['limite'] and _re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', str(lim)) else ''      # prazo com hora (para o filtro "expira em X horas")
    except RuntimeError as exc:
        saida['erro'] = 'Envio não consultado (%s)' % str(exc)[:40]
    if somente_a_enviar:
        if _motivo_fora(saida, etapa, vence_ate, vence_apos, expira_ate):
            saida['pular'] = True                   # já enviado, Full ou fora da etapa escolhida: não entra na lista, então nem consulta o frete
            return saida
    try:
        saida['frete'] = _frete_vendedor(_ml(tk, '/shipments/%s/costs' % env), vendedor)
        if saida['frete'] is None:
            saida['erro'] = saida['erro'] or 'Frete não informado pelo Mercado Livre'
    except RuntimeError as exc:
        saida['erro'] = saida['erro'] or 'Frete não consultado (%s)' % str(exc)[:40]
    pack = ped.get('pack_id')
    if pack and saida['frete'] is not None:
        try:
            ped_pack = _ml(tk, '/packs/%s' % pack)
            n = len((ped_pack or {}).get('orders') or [])
            if n > 1:
                saida['divisor'] = n
            elif n == 0:
                saida['frete'], saida['erro'] = None, 'Carrinho não conferido'
        except RuntimeError:
            saida['frete'], saida['erro'] = None, 'Carrinho não conferido'
    return saida


def _motivo_fora(saida, etapa='todos', vence_ate=None, vence_apos=None, expira_ate=None):
    """Por que um envio NÃO entra na lista "a enviar" (texto simples), ou '' se entra. Mesma regra da busca."""
    st, sub, log = saida['status_envio'].lower(), saida['substatus'].lower(), saida['logistica']
    if log == 'fulfillment':
        return 'É do Full (o Mercado Livre envia); esta lista é só do que você despacha'
    if st in FINAIS_ENVIO:
        return 'O envio já está como "%s" (não espera mais despacho)' % rotulo_envio(saida['status_envio'], saida['substatus'])
    permitidos = ETAPAS_ENVIO.get(etapa)
    if permitidos is not None and not ((st == 'ready_to_ship' and sub in permitidos) or (sub == 'buffered' and 'buffered' in permitidos)):     # aguardando (buffered) vem como status pending
        return 'Está em "%s", que fica fora da etapa escolhida na tela. Escolha "Todos os que esperam envio" na etapa para ver' % rotulo_envio(saida['status_envio'], saida['substatus'])
    if expira_ate is not None and saida.get('limite_dt'):          # "expira em até X horas" (como o Modo Rápido do UpSeller): vencidos entram; sem prazo conhecido entra
        try:
            if datetime.fromisoformat(saida['limite_dt']) > expira_ate:
                return 'O prazo de envio é %s/%s %s, depois do limite do filtro "expira em até X horas"' % (saida['limite_dt'][8:10], saida['limite_dt'][5:7], saida['limite_dt'][11:16])
        except ValueError:
            pass
    if sub in SUBS_NA_COLETA:                  # etiqueta já impressa / pacote pronto: sai na próxima coleta (hoje), não importa o prazo de despacho
        return 'O pacote já está pronto e sai na coleta de hoje (não é da lista "Próximos dias")' if vence_apos else ''
    if vence_ate and saida['limite'] and saida['limite'] > vence_ate:
        return 'O prazo de envio é %s/%s (lista "Próximos dias", não "Envios de hoje")' % (saida['limite'][8:10], saida['limite'][5:7])
    if vence_apos and saida['limite'] and saida['limite'] <= vence_apos:
        return 'O prazo de envio é %s/%s (lista "Envios de hoje", não "Próximos dias")' % (saida['limite'][8:10], saida['limite'][5:7])
    return ''


def _pedidos_irmaos(tk, ped):
    """Quando o ML separa um pedido em pacotes, o original fica cancelado e nascem números novos (no mesmo carrinho).
    Devolve ' Foi separado em outros pedidos: N (situação do envio), ...' ou um aviso, sem nunca falhar."""
    pack = ped.get('pack_id')
    novos = []
    if pack:
        try:
            for o in ((_ml(tk, '/packs/%s' % pack) or {}).get('orders') or [])[:6]:
                oid = str((o or {}).get('id') or '')
                if not oid or oid == str(ped.get('id')):
                    continue
                try:
                    p2 = _ml(tk, '/orders/%s' % oid) or {}
                    sit = str(p2.get('status') or '')
                    env = (p2.get('shipping') or {}).get('id')
                    if sit == 'paid' and env:
                        sh = _ml(tk, '/shipments/%s' % env) or {}
                        sit = rotulo_envio(str(sh.get('status') or ''), str(sh.get('substatus') or '')) if sh.get('status') else sit
                    novos.append('%s (%s)' % (oid, sit or 'sem situação'))
                except (RuntimeError, ValueError, AttributeError):
                    novos.append(oid)
        except (RuntimeError, ValueError, AttributeError):
            pass
    vivos = [n for n in novos if '(cancelled)' not in n and '(cancelado)' not in n]
    if vivos:
        return '. Outros pedidos do mesmo carrinho: ' + ', '.join(vivos)
    if novos:
        return '. Os outros pedidos do carrinho também estão cancelados. Se a venda foi separada em pacotes, os números novos aparecem no Mercado Livre (procure pela venda lá)'
    return '. Se o Mercado Livre separou a venda em pacotes, os números de venda são novos (procure pela venda no Mercado Livre); o UpSeller continua mostrando o número antigo como "Cancelado"'


def diagnosticar_pedido(conta, pedido, etapa='todos', vence_ate=None, vence_apos=None, de=None, ate=None, expira_ate=None):
    """Explica por que um pedido (número da plataforma, como no UpSeller) não apareceu na lista. Só leitura.

    Devolve {'pedido', 'conta', 'motivo', 'situacao'}; 'motivo' vazio = deveria ter aparecido.
    """
    saida = {'pedido': pedido, 'conta': conta, 'motivo': '', 'situacao': ''}
    contas = [conta] + [x for x in CONTAS if x != conta] if conta in CONTAS else list(CONTAS)     # a conta indicada primeiro; a outra só se não achar
    achou = None
    for c in contas:
        try:
            tk = token(c)
            vendedor = _vendedor(c, tk)
        except (RuntimeError, FonteIndisponivel):
            continue
        try:
            ped = _ml(tk, '/orders/%s' % pedido)
        except RuntimeError:
            ped = None
        if not (isinstance(ped, dict) and str(ped.get('id')) == str(pedido)):      # o número do UpSeller pode ser o do carrinho (pack): usa o 1º pedido dele
            try:
                outros = ((_ml(tk, '/packs/%s' % pedido) or {}).get('orders') or [])
                ped = _ml(tk, '/orders/%s' % (outros[0] or {}).get('id')) if outros else None
            except (RuntimeError, ValueError, AttributeError, IndexError):
                ped = None
            if isinstance(ped, dict):
                ped = dict(ped, id=pedido)                                           # só para passar na conferência abaixo
        if isinstance(ped, dict) and str(ped.get('id')) == str(pedido) and str((ped.get('seller') or {}).get('id') or '') in ('', str(vendedor)):
            achou = (c, tk, vendedor, ped)
            break
    if not achou:
        saida['motivo'] = 'Não encontrei este pedido no Mercado Livre%s. Confira o número ou a conta' % ''
        return saida
    c, tk, vendedor, ped = achou
    saida['conta'] = c
    status = str(ped.get('status') or '')
    data = str(ped.get('date_created') or '')[:10]
    if status != 'paid':
        saida['motivo'] = 'O pedido está como "%s" no Mercado Livre (a lista só traz pedidos pagos)' % ({'cancelled': 'cancelado', 'invalid': 'inválido', 'payment_required': 'aguardando pagamento'}.get(status, status or 'sem situação'))
        if status == 'cancelled':
            saida['motivo'] += _pedidos_irmaos(tk, ped)
        return saida
    if de and ate and data and not (de <= data <= ate):
        saida['motivo'] = 'O pedido é de %s/%s, fora do período lido (%s a %s)' % (data[8:10], data[5:7], de[8:10] + '/' + de[5:7], ate[8:10] + '/' + ate[5:7])
        return saida
    env = _envio_do_pedido(tk, vendedor, ped, False)
    if env.get('erro') == 'Pedido sem envio':
        saida['motivo'] = 'O pedido não tem envio no Mercado Livre'
        return saida
    saida['situacao'] = rotulo_envio(env['status_envio'], env['substatus']) if env['status_envio'] else 'Não consultado'
    saida['motivo'] = _motivo_fora(env, etapa, vence_ate, vence_apos, expira_ate)
    if not saida['motivo'] and not env['status_envio']:
        saida['motivo'] = 'Não consegui ler o envio agora (%s). Busque de novo' % (env['erro'] or 'sem resposta')
    return saida


def _itens_dos_pedidos(conta, tk, ativos, envios):
    """Linhas (uma por item) com venda, tarifa, frete e envio, a partir dos pedidos e dos envios já lidos."""
    precos_ml = {}
    mlbs = sorted({str((r.get('item') or {}).get('id') or '') for p in ativos for r in (p.get('order_items') or []) if isinstance(r, dict)})
    mlbs = [m for m in mlbs if _MLB.match(m)]
    for i in range(0, len(mlbs), 20):              # preço atual dos anúncios (só para mostrar na tela; falha não vira zero)
        try:
            for x in _ml(tk, '/items', {'ids': ','.join(mlbs[i:i + 20]), 'attributes': 'id,price'}) or []:
                corpo = x.get('body') if isinstance(x, dict) else None
                if isinstance(corpo, dict) and _numero(corpo.get('price')) is not None:
                    precos_ml[str(corpo.get('id'))] = round(_numero(corpo['price']), 2)
        except (RuntimeError, ValueError):
            pass
    itens = []
    for ped, env in zip(ativos, envios):
        if env.get('pular'):
            continue
        regs = [r for r in (ped.get('order_items') or []) if isinstance(r, dict)]
        totais = []
        for r in regs:
            try:
                q = int(r.get('quantity') or 0)
            except (TypeError, ValueError):
                q = 0
            totais.append((q, round(max(0, q) * (_numero(r.get('unit_price')) or 0.0), 2)))
        soma = sum(t for _, t in totais)
        frete_pedido = None if env['frete'] is None else env['frete'] / env['divisor']
        for r, (q, venda) in zip(regs, totais):
            it = r.get('item') or {}
            mlb = str(it.get('id') or '')
            if not _MLB.match(mlb) or q <= 0:
                continue
            fee = _numero(r.get('sale_fee'))
            obs = env['erro']
            if env['divisor'] > 1:
                obs = (obs + '; ' if obs else '') + 'Carrinho com %d pedidos: frete dividido igualmente' % env['divisor']
            itens.append({
                'conta': conta, 'pedido': str(ped.get('id') or ''), 'pack_id': str(ped.get('pack_id') or ''), 'envio_id': env.get('envio_id', ''), 'data': str(ped.get('date_created') or '')[:16].replace('T', ' '),
                'mlb': mlb, 'sku': str(it.get('seller_sku') or it.get('seller_custom_field') or r.get('seller_sku') or '').strip()[:80],
                'titulo': str(it.get('title') or '')[:200], 'quantidade': q, 'venda': venda,
                'tarifa': None if fee is None else round(abs(fee) * q, 2),
                'frete': None if frete_pedido is None else round(frete_pedido * (venda / soma if soma > 0 else 1.0 / max(1, len(regs))), 2),
                'grupo': _grupo_envio(env['logistica'], env['status_envio']), 'logistica': env['logistica'], 'status_envio': env['status_envio'], 'substatus': env['substatus'],
                'status_rotulo': (rotulo_envio(env['status_envio'], env['substatus']) if env['status_envio'] else 'Não consultado') + (' · enviar até %s/%s%s' % (env['limite'][8:10], env['limite'][5:7], (' ' + env['limite_dt'][11:16]) if env['limite_dt'] else '') if env['limite'] and env['status_envio'].lower() not in FINAIS_ENVIO else ''),
                'limite_envio': env['limite'], 'limite_dt': env['limite_dt'], 'preco_ml': precos_ml.get(mlb), 'obs': obs,
            })
    return itens


def pedidos_financeiros(conta, inicio, fim, offset=0, limite=20, somente_a_enviar=False, etapa='todos', vence_ate=None, vence_apos=None, expira_ate=None):
    """Uma página (até 20 pedidos pagos) com venda, tarifa e frete por item. Só leitura.

    Devolve {'itens': [...], 'total', 'proximo', 'concluido'}. Cada item: conta, pedido, data, mlb, sku, titulo,
    quantidade, venda, tarifa (None se o ML não informou), frete (parte do vendedor; None se desconhecido),
    grupo (a_enviar | enviado | full), logistica, status_envio, obs.
    """
    tk = token(conta)
    vendedor = _vendedor(conta, tk)
    params = {'seller': vendedor, 'order.date_created.from': inicio + 'T00:00:00.000-03:00', 'order.date_created.to': fim + 'T23:59:59.999-03:00',
              'order.status': 'paid', 'sort': 'date_asc', 'limit': int(limite), 'offset': int(offset)}
    try:
        dados = _ml(tk, '/orders/search', params)
    except RuntimeError as exc:
        raise FonteIndisponivel('Não consegui ler os pedidos do Mercado Livre agora (%s). Tente de novo.' % str(exc)[:80])
    if not isinstance(dados, dict) or not isinstance(dados.get('results'), list):
        raise FonteIndisponivel('O Mercado Livre respondeu os pedidos de um jeito inesperado.')
    pedidos = [p for p in dados['results'] if isinstance(p, dict)]
    ativos = [p for p in pedidos if str(p.get('status') or '') not in ('cancelled', 'invalid')]
    with ThreadPoolExecutor(max_workers=6) as ex:
        envios = list(ex.map(lambda p: _envio_do_pedido(tk, vendedor, p, somente_a_enviar, etapa, vence_ate, vence_apos, expira_ate), ativos))
    itens = _itens_dos_pedidos(conta, tk, ativos, envios)
    pag = dados.get('paging') or {}
    total = int(pag.get('total') or 0)
    proximo = int(offset) + len(pedidos)
    return {'itens': itens, 'total': total, 'proximo': proximo, 'concluido': not pedidos or proximo >= total}


def salvar_relatorio(payload):
    """Grava o relatório do dia na planilha (Apps Script, ação 'vendas_relatorio'). Devolve {'abas': [...]}."""
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        raise FonteIndisponivel('A planilha não está configurada (EXP_SHEETS_URL e EXP_SHEETS_KEY no Render).')
    try:
        r = _SESS.post(url, json=dict(payload, chave=chave, acao='vendas_relatorio'), timeout=(5, 55), allow_redirects=True)
        r.raise_for_status()
        dados = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise FonteIndisponivel('Não consegui gravar na planilha agora (%s). Nada foi perdido: tente de novo.' % str(exc)[:80])
    if not isinstance(dados, dict) or dados.get('ok') is not True:
        raise FonteIndisponivel('A planilha ainda não aceita o relatório do dia. Falta colocar o trecho novo (V8) no Apps Script.')
    return {'abas': [str(a) for a in dados.get('abas') or []]}


def atualizar_referencia(conta, itens):
    """Avisa a planilha de preços (mesma ação do painel antigo) que o preço do anúncio mudou. Só tenta; falha não desfaz nada.

    itens: [{'mlb', 'preco_referencia_anterior', 'preco_atual_ml'}], até 5. Devolve {mlb: texto}."""
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    saida = {}
    for i in range(0, len(itens), 5):
        lote = itens[i:i + 5]
        try:
            r = _SESS.post(url, json={'chave': chave, 'acao': 'catalogo_atualizar_precos_referencia', 'conta': conta, 'itens': lote}, timeout=(5, 35), allow_redirects=True)
            r.raise_for_status()
            dados = r.json()
            if not isinstance(dados, dict) or dados.get('ok') is not True:
                raise ValueError('resposta inesperada')
            por_id = {x.get('mlb'): x.get('status') for x in dados.get('resultados', []) if isinstance(x, dict)}
            for x in lote:
                saida[x['mlb']] = str(por_id.get(x['mlb']) or 'sem confirmação da planilha')
        except (requests.RequestException, ValueError) as exc:
            for x in lote:
                saida[x['mlb']] = 'não atualizada: ' + str(exc)[:60]
    return saida


def registrar_precos(conta, itens):
    """Anota na aba 'Preços atualizados <conta>' da planilha o que foi alterado (Apps Script, ação 'vendas_precos'). Só tenta: falha não desfaz o preço. Devolve um texto curto."""
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        return 'não anotado na planilha (planilha não configurada)'
    try:
        r = _SESS.post(url, json={'chave': chave, 'acao': 'vendas_precos', 'conta': conta, 'itens': itens}, timeout=(5, 35), allow_redirects=True)
        r.raise_for_status()
        dados = r.json()
        if not isinstance(dados, dict) or dados.get('ok') is not True:
            return 'não anotado na planilha (falta atualizar o Apps Script)'
        return 'anotado na aba "%s": %s novo(s), %s já estava(m) lá' % (dados.get('aba', ''), dados.get('novos', 0), dados.get('repetidos', 0))
    except (requests.RequestException, ValueError) as exc:
        return 'não anotado na planilha: ' + str(exc)[:60]


def _sheets_post(corpo, espera=35):
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        raise FonteIndisponivel('A planilha não está configurada (EXP_SHEETS_URL e EXP_SHEETS_KEY no Render).')
    try:
        r = _SESS.post(url, json=dict(corpo, chave=chave), timeout=(5, espera), allow_redirects=True)
        r.raise_for_status()
        dados = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise FonteIndisponivel('Não consegui falar com a planilha agora (%s). Tente de novo.' % str(exc)[:80])
    if not isinstance(dados, dict) or dados.get('ok') is not True:
        raise FonteIndisponivel('A planilha ainda não tem o trecho das etiquetas (arquivo apps_script/etiquetas.gs). Falta colocar no Apps Script.')
    return dados


def etiquetas_registrar(registros):
    """Grava na aba 'Etiquetas' da planilha a situação (GERADA/CANCELADA) de cada pedido. Quem repete é atualizado, nunca duplicado."""
    dados = _sheets_post({'acao': 'etiquetas_registrar', 'registros': registros})
    return {'gravados': int(dados.get('gravados') or 0), 'atualizados': int(dados.get('atualizados') or 0)}


def etiquetas_ler(conta, pedidos):
    """{pedido: {'situacao', 'data'}} do que já foi marcado na aba 'Etiquetas'. Pedido que não está na aba simplesmente não aparece."""
    dados = _sheets_post({'acao': 'etiquetas_ler', 'conta': conta, 'pedidos': pedidos}, espera=45)
    saida = {}
    for k, v in (dados.get('etiquetas') or {}).items():
        if isinstance(v, dict) and v.get('situacao') in ('GERADA', 'CANCELADA'):
            saida[str(k)] = {'situacao': v['situacao'], 'data': str(v.get('data') or '')[:20]}
    return saida


def impostos_conta(conta):
    """Imposto/embalagem (% do valor da venda) da aba TaxasML da conta: {'normal': %, 'full': %}. Sempre lido na hora (sem cache): mudou na planilha, vale na próxima busca. Falha vira erro explicativo, nunca zero."""
    if conta not in CONTAS:
        raise FonteIndisponivel('Conta inválida.')
    url, chave = os.getenv('EXP_SHEETS_URL', '').strip(), os.getenv('EXP_SHEETS_KEY', '').strip()
    if not url.startswith('https://script.google.com/') or not chave:
        raise FonteIndisponivel('A planilha não está configurada (EXP_SHEETS_URL e EXP_SHEETS_KEY no Render).')
    try:
        r = _SESS.post(url, json={'chave': chave, 'acao': 'catalogo_taxas', 'conta': conta}, timeout=(5, 25), allow_redirects=True)
        r.raise_for_status()
        dados = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise FonteIndisponivel('Não consegui ler os impostos da planilha (%s).' % str(exc)[:80])
    if not isinstance(dados, dict) or dados.get('ok') is not True or dados.get('conta') != conta:
        raise FonteIndisponivel('A aba TaxasML não confirmou os dados da conta %s.' % conta)
    saida = {}
    for nome, campo in (('normal', 'imposto_embalagem_normal_pct'), ('full', 'imposto_embalagem_full_pct')):
        v = _numero(dados.get(campo))
        if v is None or not 0 <= v < 60:
            raise FonteIndisponivel('Imposto "%s" inválido ou vazio na aba TaxasML (%s).' % (nome, conta))
        saida[nome] = v
    return saida


def pedidos_por_numero(conta_dica, numeros):
    """Linhas (como as da busca, sem filtro de etapa/prazo) dos pedidos que o UpSeller mostra e a busca não trouxe.

    O número do UpSeller pode ser do pedido ou do carrinho (pack): tenta o pedido, depois o carrinho.
    Devolve {'itens': [...], 'ignorados': [{'pedido', 'motivo'}]}. Só leitura.
    """
    achados, ignorados = {}, []
    for n in numeros:
        ordem = [conta_dica] + [x for x in CONTAS if x != conta_dica] if conta_dica in CONTAS else list(CONTAS)
        achou = False
        for c in ordem:
            try:
                tk = token(c)
                vendedor = _vendedor(c, tk)
            except (RuntimeError, FonteIndisponivel):
                continue
            peds = []
            try:
                p = _ml(tk, '/orders/%s' % n)
                if isinstance(p, dict) and str(p.get('id')) == str(n):
                    peds = [p]
            except RuntimeError:
                pass
            if not peds:
                try:
                    for o in ((_ml(tk, '/packs/%s' % n) or {}).get('orders') or [])[:10]:
                        peds.append(_ml(tk, '/orders/%s' % (o or {}).get('id')))
                except (RuntimeError, ValueError, AttributeError):
                    peds = []
            peds = [p for p in peds if isinstance(p, dict) and str((p.get('seller') or {}).get('id') or '') in ('', str(vendedor))]
            if not peds:
                continue
            achou = True
            for p in peds:
                if str(p.get('status') or '') != 'paid':
                    ignorados.append({'pedido': n, 'motivo': 'Pedido %s está como "%s" no Mercado Livre (não entra nas vendas)' % (p.get('id'), p.get('status') or 'sem situação')})
                else:
                    achados.setdefault(c, []).append((n, tk, vendedor, p))
            break
        if not achou:
            ignorados.append({'pedido': n, 'motivo': 'Não encontrei este pedido no Mercado Livre. Confira o número ou a conta'})
    itens = []
    for c, lista in achados.items():
        tk, vendedor = lista[0][1], lista[0][2]
        peds = [x[3] for x in lista]
        with ThreadPoolExecutor(max_workers=6) as ex:
            envios = list(ex.map(lambda p: _envio_do_pedido(tk, vendedor, p, False), peds))
        for it in _itens_dos_pedidos(c, tk, peds, envios):
            it['pedido_upseller'] = next((x[0] for x in lista if str(x[3].get('id')) == it['pedido']), '')
            itens.append(it)
    return {'itens': itens, 'ignorados': ignorados}
