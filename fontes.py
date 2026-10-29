"""Fontes de dados do painel: custo (planilha de custos via Apps Script) e frete (API do Mercado Livre).

Tudo é somente leitura. Falha de consulta NUNCA vira zero: devolve None + motivo.

Variáveis de ambiente:
  EXP_SHEETS_URL, EXP_SHEETS_KEY   -> Apps Script da planilha de custos (aba DadosBling, coluna Custo)
  EXP_TOKEN_URL_ML19, EXP_TOKEN_URL_ML44 -> Apps Scripts que devolvem o access_token de cada conta
"""
import os
import threading
import time
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


def _consultar_custos(url, chave, lista):
    """{sku_pedido: custo_bruto} para uma lista de SKUs (em lotes)."""
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
                saida[sku] = _numero(valor.get('custo'))
    return saida


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
    try:
        r = _SESS.get(url, timeout=(5, 30), allow_redirects=True)
        r.raise_for_status()
        try:
            dados = r.json()
        except ValueError:
            dados = r.text.strip()
    except requests.RequestException as exc:
        raise FonteIndisponivel('Não consegui o acesso ao Mercado Livre agora (%s).' % str(exc)[:80])
    valor = (dados.get('access_token') or dados.get('accessToken') or dados.get('token')) if isinstance(dados, dict) else str(dados).strip().strip('"')
    if not valor:
        raise FonteIndisponivel('O Mercado Livre não devolveu o acesso da conta %s.' % conta)
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
