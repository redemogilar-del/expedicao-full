"""Aba Perguntas: lê as perguntas dos anúncios e responde pelo painel (QUINTA escrita no ML). Sem Flask.

Regras (iguais às das outras escritas): só envia com confirmação, relê a pergunta na hora,
confere no Mercado Livre depois de enviar, nunca repete o envio sozinho e nunca dá como feito sem conferência.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import re
import threading
import time

import requests

import envios
import fontes
import skus

CONFIRMACAO = 'RESPONDER_PERGUNTA'
MAX_TEXTO = 2000                 # limite do Mercado Livre
POR_PAGINA = 50
MAX_PAGINAS = 4                  # até 200 perguntas por conta em cada busca
MAX_APELIDOS = 40
JANELA = 60
SITUACOES = {'a_responder': 'UNANSWERED', 'respondidas': 'ANSWERED', 'todas': None}
PERIODOS_PRAZO = (('weekdays_working_hours', 'Seg a sex, das 9 às 18h'), ('weekdays_extra_hours', 'Seg a sex, das 18 às 00h'), ('weekend', 'Sábado e domingo'))
_ID = re.compile(r'^\d{1,20}$')
_THUMB = 'https://http2.mlstatic.com/'
_CAMPOS = 'id,title,status,price,available_quantity,seller_custom_field,attributes,variations,shipping,permalink,secure_thumbnail,thumbnail'
_RECENTES, _LOCK = {}, threading.Lock()


def _reservar(chave):
    agora = time.monotonic()
    with _LOCK:
        for k in [k for k, t in _RECENTES.items() if agora - t > JANELA]:
            _RECENTES.pop(k, None)
        if chave in _RECENTES:
            return False
        _RECENTES[chave] = agora
        return True


def _liberar(chave):
    with _LOCK:
        _RECENTES.pop(chave, None)


def _falha(conta, exc, oque):
    texto = str(exc)
    if re.search(r'\b40[13]\b', texto):
        return fontes.FonteIndisponivel('O Mercado Livre não liberou o acesso às perguntas da conta %s. Confira se o aplicativo dessa conta tem permissão de perguntas no painel de desenvolvedor do Mercado Livre.' % conta)
    return fontes.FonteIndisponivel('Não consegui %s da conta %s agora (%s). Tente de novo.' % (oque, conta, texto[:80]))


def _conta(conta):
    """(token, id do vendedor). Falha do Mercado Livre nesta etapa vira aviso claro, não 'algo deu errado'."""
    tk = fontes.token(conta)
    try:
        return tk, fontes._vendedor(conta, tk)
    except (RuntimeError, ValueError) as exc:
        if isinstance(exc, fontes.FonteIndisponivel):
            raise
        raise _falha(conta, exc, 'identificar a conta')


def _data(v):
    try:
        d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except ValueError:
        return None


def _thumb(corpo):
    url = str(corpo.get('secure_thumbnail') or corpo.get('thumbnail') or '').replace('http://', 'https://', 1)
    return url if url.startswith(_THUMB) else ''


def _skus(corpo):
    vistos = []
    for lugar in skus.locais(corpo):
        s = lugar['scf'] or lugar['attr']
        s = str(s).strip() if s not in (None, '') else ''
        if s and s not in vistos:
            vistos.append(s)
    return vistos


def _sku(corpo):
    return ', '.join(_skus(corpo)[:3])[:80]


def _itens(tk, ids):
    """{mlb: dados do anúncio} em lotes de 20. Falhou um lote: as perguntas aparecem sem os dados do produto."""
    ids = sorted({i for i in ids if fontes._MLB.match(i)})

    def ler(grupo):
        try:
            return fontes._ml(tk, '/items', {'ids': ','.join(grupo), 'attributes': _CAMPOS})
        except (RuntimeError, ValueError):
            return None

    grupos = [ids[i:i + 20] for i in range(0, len(ids), 20)]
    saida, falhou = {}, False
    if not grupos:
        return saida, falhou
    with ThreadPoolExecutor(max_workers=4) as ex:
        for lote in ex.map(ler, grupos):
            falhou = falhou or lote is None
            for x in lote if isinstance(lote, list) else []:
                corpo = x.get('body') if isinstance(x, dict) and x.get('code') == 200 else None
                if not isinstance(corpo, dict) or not corpo.get('id'):
                    continue
                link = str(corpo.get('permalink') or '')
                saida[corpo['id']] = {'titulo': str(corpo.get('title') or ''), 'sku': _sku(corpo), 'skus': [x for x in _skus(corpo)[:10] if len(x) <= 80], 'preco': corpo.get('price'), 'estoque': corpo.get('available_quantity'),
                                      'status': str(corpo.get('status') or ''), 'frete_gratis': bool((corpo.get('shipping') or {}).get('free_shipping')),
                                      'thumb': _thumb(corpo), 'link': link if link.startswith('https://') else ''}
    return saida, falhou


def _apelidos(tk, ids):
    """{id: apelido}. Só uma tentativa por comprador: sem apelido, a tela mostra o número do comprador."""
    def ler(uid):
        try:
            r = fontes._SESS.get(fontes.API + '/users/' + uid, headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 8))
            return uid, str(r.json().get('nickname') or '') if r.ok else ''
        except (requests.RequestException, ValueError, AttributeError):
            return uid, ''

    ids = sorted({i for i in ids if i})[:MAX_APELIDOS]
    if not ids:
        return {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(ler, ids))


def _resposta(q):
    a = q.get('answer')
    if not isinstance(a, dict):
        return None
    return {'texto': str(a.get('text') or ''), 'data': str(a.get('date_created') or ''), 'situacao': str(a.get('status') or '').upper()}


def listar(conta, situacao='a_responder', dias=None):
    """Perguntas da conta, das mais novas para as mais antigas (até 200). dias=None = todo o período."""
    if situacao not in SITUACOES:
        raise envios.ArquivoInvalido('Situação de pergunta inválida.')
    tk, vendedor = _conta(conta)
    corte = datetime.now(timezone.utc) - timedelta(days=dias) if dias else None
    params = {'seller_id': vendedor, 'api_version': 4, 'sort_fields': 'date_created', 'sort_types': 'DESC', 'limit': POR_PAGINA}
    if SITUACOES[situacao]:
        params['status'] = SITUACOES[situacao]
    achadas, total, fim = [], 0, False
    for pagina in range(MAX_PAGINAS):
        try:
            d = fontes._ml(tk, '/questions/search', dict(params, offset=pagina * POR_PAGINA))
        except (RuntimeError, ValueError) as exc:
            raise _falha(conta, exc, 'ler as perguntas')
        lista = (d or {}).get('questions') if isinstance(d, dict) else None
        lista = lista if isinstance(lista, list) else []
        total = d.get('total') if isinstance(d, dict) and isinstance(d.get('total'), int) else len(lista)
        for q in lista:
            quando = _data(q.get('date_created')) if isinstance(q, dict) else None
            if corte and quando and quando < corte:
                fim = True
                break
            if isinstance(q, dict) and q.get('id') is not None:
                achadas.append(q)
        if fim or len(lista) < POR_PAGINA or (pagina + 1) * POR_PAGINA >= total:
            fim = True
            break
    itens, falhou = _itens(tk, [str(q.get('item_id') or '') for q in achadas])
    apelidos = _apelidos(tk, [str((q.get('from') or {}).get('id') or '') for q in achadas])
    saida = []
    for q in achadas:
        mlb, comprador = str(q.get('item_id') or ''), str((q.get('from') or {}).get('id') or '')
        saida.append({'id': str(q['id']), 'conta': conta, 'texto': str(q.get('text') or ''), 'situacao': str(q.get('status') or '').upper(), 'data': str(q.get('date_created') or ''),
                      'mlb': mlb, 'item': itens.get(mlb), 'comprador_id': comprador, 'comprador': apelidos.get(comprador, ''), 'resposta': _resposta(q)})
    return {'conta': conta, 'perguntas': saida, 'total': total, 'incompleto': not fim, 'itens_falharam': falhou}


def _minutos(v):
    if isinstance(v, dict):
        v = v.get('response_time')
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def prazo(conta):
    """Tempo médio de resposta dos últimos 14 dias, por período. O Mercado Livre atualiza uma vez por dia."""
    tk, vendedor = _conta(conta)
    try:
        r = fontes._SESS.get(fontes.API + '/users/%s/questions/response_time' % vendedor, headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 18))
        if r.status_code == 404:                     # conta sem perguntas no período
            return {'conta': conta, 'total': None, 'periodos': [], 'aumento': None}
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise _falha(conta, exc, 'ler o prazo de resposta')
    d = d if isinstance(d, dict) else {}
    aumentos = [x.get('sales_percent_increase') for x in (d.get(c) for c, _ in PERIODOS_PRAZO) if isinstance(x, dict) and isinstance(x.get('sales_percent_increase'), (int, float))]
    return {'conta': conta, 'total': _minutos(d.get('total')), 'aumento': max(aumentos) if aumentos else None,
            'periodos': [{'chave': c, 'nome': n, 'minutos': _minutos(d.get(c))} for c, n in PERIODOS_PRAZO]}


def validar_resposta(c):
    """(conta, id da pergunta, texto) ou erro claro."""
    if c.get('confirmacao') != CONFIRMACAO:
        raise envios.ArquivoInvalido('Confirmação obrigatória para responder.')
    conta, pid = c.get('conta'), str(c.get('pergunta') or '').strip()
    if conta not in fontes.CONTAS:
        raise envios.ArquivoInvalido('Escolha a conta do Mercado Livre.')
    if not _ID.match(pid):
        raise envios.ArquivoInvalido('Pergunta inválida.')
    texto = str(c.get('texto') or '').replace('\r\n', '\n').strip()
    if not texto:
        raise envios.ArquivoInvalido('Escreva a resposta antes de enviar.')
    if len(texto) > MAX_TEXTO:
        raise envios.ArquivoInvalido('A resposta passou de %d caracteres. Encurte o texto.' % MAX_TEXTO)
    return conta, pid, texto


def _ler_pergunta(tk, pid):
    q = fontes._ml(tk, '/questions/' + pid, {'api_version': 4})
    if not isinstance(q, dict) or str(q.get('id')) != pid:
        raise ValueError('a API não retornou esta pergunta')
    return q


def _motivo(resp):
    try:
        d = resp.json()
        msg = (d.get('message') or d.get('error')) if isinstance(d, dict) else ''
    except ValueError:
        msg = ''
    return str(msg or resp.text or '')[:200]


def responder(conta, tk, vendedor, pid, texto):
    """Envia UMA resposta. status: OK / PULADO / ERRO / NAO_CONFIRMADO. Nunca repete o envio sozinho."""
    r = {'pergunta': pid, 'status': 'PULADO', 'mensagem': '', 'resposta': None}
    chave = (conta, pid)
    try:
        q = _ler_pergunta(tk, pid)
        if str(q.get('seller_id')) != str(vendedor):
            r['mensagem'] = 'Esta pergunta não pertence à conta %s.' % conta
            return r
        st = str(q.get('status') or '').upper()
        if st != 'UNANSWERED':
            r.update(mensagem='Esta pergunta já foi respondida ou encerrada. Atualize a lista.', resposta=_resposta(q))
            return r
    except (RuntimeError, ValueError) as exc:
        r.update(status='ERRO', mensagem='Nada foi enviado: não consegui reler a pergunta (%s). Tente de novo.' % str(exc)[:100])
        return r
    if not _reservar(chave):
        r['mensagem'] = 'Esta pergunta acabou de receber uma resposta. Atualize a lista antes de tentar de novo.'
        return r
    resp = None
    try:
        resp = fontes._SESS.post(fontes.API + '/answers', json={'question_id': int(pid), 'text': texto}, headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 25))
    except requests.RequestException:
        pass                                         # pode ter chegado: não repete; a conferência abaixo decide
    if resp is not None and not resp.ok:
        _liberar(chave)                              # o ML recusou: pode corrigir o texto e enviar de novo
        r.update(status='ERRO', mensagem='O Mercado Livre recusou a resposta (HTTP %s): %s' % (resp.status_code, _motivo(resp)))
        return r
    try:
        depois = _ler_pergunta(tk, pid)
    except (RuntimeError, ValueError):
        r.update(status='NAO_CONFIRMADO', mensagem='Não deu para confirmar o envio. Atualize a lista antes de tentar de novo, para não responder duas vezes.')
        return r
    resposta = _resposta(depois)
    if str(depois.get('status') or '').upper() == 'ANSWERED' and resposta and resposta['texto'].strip() == texto:
        r.update(status='OK', mensagem='Resposta enviada e conferida no Mercado Livre.', resposta=resposta)
    else:
        r.update(status='NAO_CONFIRMADO', mensagem='A resposta ainda não aparece no Mercado Livre. Atualize a lista em alguns minutos antes de tentar de novo.', resposta=resposta)
    return r
