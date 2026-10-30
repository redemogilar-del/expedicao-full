"""Troca de SKU em massa nos anúncios do Mercado Livre (aba Trocar SKU).

Buscar é somente leitura: varre os anúncios da conta (em passos pequenos) e mostra o que mudaria.
Aplicar é a escrita: exige seleção + confirmação, lê o anúncio de novo na hora, só mexe se o SKU ainda for
o que a pessoa viu, e confere no Mercado Livre depois de gravar. Nada é dado como feito sem conferência.

O SKU do anúncio pode estar em dois campos (`seller_custom_field` e o atributo `SELLER_SKU`), no anúncio ou em cada
variação. A troca mexe somente nos campos cujo valor casa com a regra e depois confere cada um.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import fontes

CONFIRMACAO = 'TROCAR_SKU'
MODOS = ('igual', 'contem', 'comeca')
ESCOPOS = ('inteiro', 'trecho')
MAX_APLICAR = 5                 # anúncios por chamada
MAX_SKU = 100
MIN_TERMO_PARCIAL = 2           # "contém" / "começa com" com 1 letra pegaria quase tudo
LOTE_VARREDURA = 100            # ids por passo da varredura
JANELA_REAPLICACAO = 60
ATIVOS = ('active', 'paused')
CAMPOS_ITEM = 'id,title,status,seller_id,seller_custom_field,attributes,variations,permalink'
_RECENTES, _LOCK = {}, threading.Lock()
_CTRL = re.compile(r'[\x00-\x1f\x7f]')


class Bloqueio(Exception):
    """Motivo, em português simples, pelo qual o anúncio não pode ser alterado."""


def validar_regra(modo, termo, novo, escopo):
    if modo not in MODOS:
        raise Bloqueio('Escolha como buscar: igual, contém ou começa com.')
    termo, novo = str(termo or '').strip(), str(novo or '').strip()
    if not termo:
        raise Bloqueio('Digite o SKU que será procurado.')
    if modo != 'igual' and len(termo) < MIN_TERMO_PARCIAL:
        raise Bloqueio('Para "contém" e "começa com", digite pelo menos %d caracteres.' % MIN_TERMO_PARCIAL)
    if escopo not in ESCOPOS:
        escopo = 'inteiro'
    if modo == 'igual':
        escopo = 'inteiro'
    if not novo:
        raise Bloqueio('Digite o SKU novo.')
    if len(novo) > MAX_SKU or _CTRL.search(novo) or _CTRL.search(termo) or len(termo) > MAX_SKU:
        raise Bloqueio('SKU inválido (máximo %d caracteres, sem caracteres especiais de controle).' % MAX_SKU)
    return modo, termo, novo, escopo


def trocar(valor, modo, termo, novo, escopo):
    """SKU novo para `valor`, ou None se `valor` não casa com a regra (maiúsculas/minúsculas não importam na busca)."""
    v = str(valor or '').strip()
    if not v:
        return None
    a, t = v.casefold(), termo.casefold()
    if modo == 'igual':
        return novo if a == t else None
    if modo == 'comeca':
        if not a.startswith(t):
            return None
        return novo if escopo == 'inteiro' else novo + v[len(termo):]
    if t not in a:
        return None
    if escopo == 'inteiro':
        return novo
    return re.sub(re.escape(termo), lambda _m: novo, v, flags=re.IGNORECASE)


def _attr_sku(lista):
    for a in lista or []:
        if isinstance(a, dict) and a.get('id') == 'SELLER_SKU':
            return str(a.get('value_name') or '').strip()
    return ''


def locais(item):
    """[{variacao: None|id, scf, attr}] — onde há SKU no anúncio (ele mesmo e cada variação)."""
    saida = []
    scf, attr = str(item.get('seller_custom_field') or '').strip(), _attr_sku(item.get('attributes'))
    if scf or attr:
        saida.append({'variacao': None, 'scf': scf, 'attr': attr})
    for v in item.get('variations') or []:
        if not isinstance(v, dict) or v.get('id') is None:
            continue
        s, a = str(v.get('seller_custom_field') or '').strip(), _attr_sku(v.get('attributes'))
        if s or a:
            saida.append({'variacao': v['id'], 'scf': s, 'attr': a})
    return saida


def _novos_campos(loc, regra):
    """{'scf': novo, 'attr': novo} só para os campos que casam com a regra e mudam de valor."""
    r = {}
    for campo in ('scf', 'attr'):
        n = trocar(loc[campo], *regra)
        if n is not None and n != loc[campo]:
            r[campo] = n
    return r


def _valida_novo(n):
    if not n or len(n) > MAX_SKU:
        raise Bloqueio('O SKU novo ficaria vazio ou com mais de %d caracteres.' % MAX_SKU)


def achados(item, regra):
    """Linhas de pré-visualização para um anúncio (uma por local com SKU que casa com a regra)."""
    saida = []
    for loc in locais(item):
        mud = _novos_campos(loc, regra)
        if not mud:
            continue
        atual = loc['scf'] or loc['attr']
        divergente = bool(loc['scf'] and loc['attr'] and loc['scf'] != loc['attr'])
        novo = mud.get('scf') or mud.get('attr')
        aviso = ''
        if divergente:
            aviso = 'Os dois campos de SKU do anúncio são diferentes (%s / %s).' % (loc['scf'], loc['attr'])
        try:
            for n in mud.values():
                _valida_novo(n)
        except Bloqueio as exc:
            aviso, novo = str(exc), ''
        saida.append({'mlb': item['id'], 'titulo': str(item.get('title') or '')[:120], 'status': str(item.get('status') or ''),
                      'variacao': loc['variacao'], 'sku_atual': atual, 'sku_novo': novo, 'aviso': aviso,
                      'permitido': item.get('status') in ATIVOS and not aviso.startswith('O SKU novo'),
                      'link': str(item.get('permalink') or '') if str(item.get('permalink') or '').startswith('https://') else ''})
    return saida


def varrer(conta, regra, scroll_id=None):
    """Um passo da varredura (até 100 anúncios). Só leitura.

    Devolve {'achados', 'lidos', 'total', 'scroll_id', 'concluido'}. O `scroll_id` volta para o próximo passo."""
    tk = fontes.token(conta)
    vendedor = fontes._vendedor(conta, tk)
    params = {'search_type': 'scan', 'limit': LOTE_VARREDURA}
    if scroll_id:
        params['scroll_id'] = str(scroll_id)[:300]
    try:
        pag = fontes._ml(tk, '/users/%s/items/search' % vendedor, params)
    except RuntimeError as exc:
        raise fontes.FonteIndisponivel('Não consegui listar os anúncios agora (%s). Tente de novo.' % str(exc)[:80])
    ids = [i for i in (pag or {}).get('results') or [] if fontes._MLB.match(str(i))]
    total = int(((pag or {}).get('paging') or {}).get('total') or 0)
    novo_scroll = (pag or {}).get('scroll_id') or scroll_id
    if not ids:
        return {'achados': [], 'lidos': 0, 'total': total, 'scroll_id': novo_scroll, 'concluido': True}
    grupos = [ids[i:i + 20] for i in range(0, len(ids), 20)]

    def ler(grupo):
        try:
            return fontes._ml(tk, '/items', {'ids': ','.join(grupo), 'attributes': CAMPOS_ITEM})
        except RuntimeError as exc:
            raise fontes.FonteIndisponivel('Não consegui ler os anúncios agora (%s). Tente de novo.' % str(exc)[:80])

    with ThreadPoolExecutor(max_workers=3) as ex:
        respostas = list(ex.map(ler, grupos))
    achou, lidos = [], 0
    for resp in respostas:
        for x in resp if isinstance(resp, list) else []:
            corpo = x.get('body') if isinstance(x, dict) else None
            if not isinstance(corpo, dict) or x.get('code') != 200 or not fontes._MLB.match(str(corpo.get('id') or '')):
                raise fontes.FonteIndisponivel('O Mercado Livre não devolveu todos os anúncios deste passo. Tente de novo.')
            if str(corpo.get('seller_id')) != str(vendedor):
                continue
            lidos += 1
            achou.extend(achados(corpo, regra))
    return {'achados': achou, 'lidos': lidos, 'total': total, 'scroll_id': novo_scroll, 'concluido': False}


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


def _corpo(mudancas):
    """mudancas: [(variacao|None, {'scf': novo, 'attr': novo})] -> corpo do PUT."""
    corpo, variacoes = {}, []
    for var, mud in mudancas:
        campos = {}
        if 'scf' in mud:
            campos['seller_custom_field'] = mud['scf']
        if 'attr' in mud:
            campos['attributes'] = [{'id': 'SELLER_SKU', 'value_name': mud['attr']}]
        if var is None:
            corpo.update(campos)
        else:
            variacoes.append(dict(campos, id=var))
    if variacoes:
        corpo['variations'] = variacoes
    return corpo


def trocar_item(conta, tk, vendedor, mlb, alvos, regra):
    """Troca o SKU dos locais `alvos` ({variacao, sku_visto}) de um anúncio. Devolve um dicionário de resultado."""
    r = {'mlb': mlb, 'status': 'PULADO', 'mensagem': '', 'trocas': []}
    try:
        item = fontes._ml(tk, '/items/' + mlb)
        if not isinstance(item, dict) or str(item.get('id')) != mlb:
            raise Bloqueio('A API não retornou este anúncio.')
        if str(item.get('seller_id')) != str(vendedor):
            raise Bloqueio('Anúncio não pertence à conta.')
        if item.get('status') not in ATIVOS:
            raise Bloqueio('Anúncio %s: não dá para alterar.' % (item.get('status') or 'sem status'))
        atuais = {l['variacao']: l for l in locais(item)}
        mudancas, previstos = [], []
        for var, visto in alvos:
            loc = atuais.get(var)
            if loc is None:
                raise Bloqueio('O SKU deste anúncio mudou desde que você viu. Busque de novo.')
            if visto not in (loc['scf'], loc['attr']):
                raise Bloqueio('O SKU mudou no Mercado Livre desde que você viu (agora: %s). Busque de novo.' % (loc['scf'] or loc['attr']))
            mud = _novos_campos(loc, regra)
            if not mud:
                raise Bloqueio('O SKU não casa mais com a busca. Busque de novo.')
            for n in mud.values():
                _valida_novo(n)
            mudancas.append((var, mud))
            previstos.append((var, loc, mud))
        if not reservar(conta, mlb):
            raise Bloqueio('Este anúncio acabou de ser alterado. Aguarde um minuto.')
        resp = fontes._SESS.put(fontes.API + '/items/' + mlb, json=_corpo(mudancas), headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 25))
        if not resp.ok:
            r.update(status='ERRO', mensagem='O Mercado Livre recusou a troca (HTTP %s): %s' % (resp.status_code, resp.text[:160]))
            return r
        conf = {l['variacao']: l for l in locais(fontes._ml(tk, '/items/' + mlb))}
        tudo_ok = True
        for var, loc, mud in previstos:
            lido = conf.get(var) or {'scf': '', 'attr': ''}
            ok = all(lido[campo] == n for campo, n in mud.items())
            tudo_ok = tudo_ok and ok
            r['trocas'].append({'variacao': var, 'sku_antes': loc['scf'] or loc['attr'], 'sku_depois': lido['scf'] or lido['attr'], 'conferido': ok})
        if tudo_ok:
            r.update(status='OK', mensagem='SKU trocado e conferido no Mercado Livre.')
        else:
            r.update(status='NAO_CONFIRMADO', mensagem='O Mercado Livre aceitou, mas ao conferir o SKU não ficou como esperado. Abra o anúncio e confira.')
    except Bloqueio as exc:
        r.update(status='PULADO', mensagem=str(exc))
    except (RuntimeError, ValueError) as exc:
        r.update(status='ERRO', mensagem='Falha ao falar com o Mercado Livre: ' + str(exc)[:120])
    return r
