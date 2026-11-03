"""Troca de SKU em massa nos anúncios do Mercado Livre (aba Trocar SKU).

Buscar é somente leitura: varre os anúncios da conta (em passos pequenos) e mostra o que mudaria.
Aplicar é a escrita: exige seleção + confirmação, lê o anúncio de novo na hora, só mexe se o SKU ainda for
o que a pessoa viu, e confere no Mercado Livre depois de gravar. Nada é dado como feito sem conferência.

O SKU do anúncio pode estar em dois campos (`seller_custom_field` e o atributo `SELLER_SKU`), no anúncio ou em cada
variação. A troca mexe somente nos campos cujo valor casa com a regra e depois confere cada um.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import fontes

CONFIRMACAO = 'TROCAR_SKU'
MODOS = ('igual', 'contem', 'comeca', 'sem_contem', 'sem_comeca', 'sem_todos')
MODOS_SEM_SKU = ('sem_contem', 'sem_comeca', 'sem_todos')     # anúncios que estão SEM SKU; o termo é buscado no TÍTULO
ESCOPOS = ('inteiro', 'trecho')
MAX_APLICAR = 10                # anúncios por chamada (trocados ao mesmo tempo)
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
        raise Bloqueio('Escolha como buscar.')
    termo, novo = str(termo or '').strip(), str(novo or '').strip()
    if len(termo) > MAX_SKU or _CTRL.search(termo) or len(novo) > MAX_SKU or _CTRL.search(novo):
        raise Bloqueio('Texto inválido (máximo %d caracteres, sem caracteres especiais de controle).' % MAX_SKU)
    if modo == 'sem_todos':                    # só olhar a lista: não troca nada
        return modo, termo, novo, 'inteiro'
    if not termo:
        raise Bloqueio('Digite o SKU que será procurado.' if modo in ('igual', 'contem', 'comeca') else 'Digite a palavra que está no título do produto.')
    if modo != 'igual' and len(termo) < MIN_TERMO_PARCIAL:
        raise Bloqueio('Digite pelo menos %d caracteres para buscar por "contém" ou "começa com".' % MIN_TERMO_PARCIAL)
    if escopo not in ESCOPOS:
        escopo = 'inteiro'
    if modo == 'igual' or modo in MODOS_SEM_SKU:
        escopo = 'inteiro'
    if not novo:
        raise Bloqueio('Digite o SKU novo.')
    return modo, termo, novo, escopo


def titulo_casa(titulo, modo, termo):
    t, a = str(termo).casefold(), str(titulo or '').strip().casefold()
    if modo == 'sem_todos':
        return True
    return a.startswith(t) if modo == 'sem_comeca' else t in a


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


def nome_variacao(item, var_id):
    """Nome da variação para a tela, ex.: 'Cor: Branco / Tamanho: G' ('' se o anúncio não tem variação ou o ML não informou)."""
    if var_id is None:
        return ''
    for v in item.get('variations') or []:
        if isinstance(v, dict) and v.get('id') == var_id:
            partes = []
            for c in v.get('attribute_combinations') or []:
                if isinstance(c, dict) and str(c.get('value_name') or '').strip():
                    nome = str(c.get('name') or '').strip()
                    partes.append((nome + ': ' if nome else '') + str(c['value_name']).strip())
            return ' / '.join(partes)[:120]
    return ''


def locais_sem_sku(item):
    """[{variacao, scf, attr}] dos lugares SEM SKU: o anúncio (se não tem variações) ou cada variação sem SKU."""
    variacoes = [v for v in item.get('variations') or [] if isinstance(v, dict) and v.get('id') is not None]
    if not variacoes:
        if not str(item.get('seller_custom_field') or '').strip() and not _attr_sku(item.get('attributes')):
            return [{'variacao': None, 'scf': '', 'attr': ''}]
        return []
    return [{'variacao': v['id'], 'scf': '', 'attr': ''} for v in variacoes
            if not str(v.get('seller_custom_field') or '').strip() and not _attr_sku(v.get('attributes'))]


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


def _achados_sem_sku(item, regra):
    modo, termo, novo, _escopo = regra
    if not titulo_casa(item.get('title'), modo, termo):
        return []
    pode = item.get('status') in ATIVOS
    aviso = ''
    if modo != 'sem_todos' and len(novo) > MAX_SKU:
        pode, aviso = False, 'O SKU novo ficaria vazio ou com mais de %d caracteres.' % MAX_SKU
    return [{'mlb': item['id'], 'titulo': str(item.get('title') or '')[:120], 'status': str(item.get('status') or ''), 'variacao': loc['variacao'], 'variacao_nome': nome_variacao(item, loc['variacao']),
             'sku_atual': '', 'sku_novo': novo if modo != 'sem_todos' else '', 'aviso': aviso, 'permitido': pode,
             'link': str(item.get('permalink') or '') if str(item.get('permalink') or '').startswith('https://') else ''}
            for loc in locais_sem_sku(item)]


def achados(item, regra):
    """Linhas de pré-visualização para um anúncio (uma por local que casa com a regra)."""
    if item.get('status') not in ATIVOS:
        return []            # só entram na lista anúncios que dá para alterar (ativos e pausados)
    if regra[0] in MODOS_SEM_SKU:
        return _achados_sem_sku(item, regra)
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
                      'variacao': loc['variacao'], 'variacao_nome': nome_variacao(item, loc['variacao']), 'sku_atual': atual, 'sku_novo': novo, 'aviso': aviso,
                      'permitido': item.get('status') in ATIVOS and not aviso.startswith('O SKU novo'),
                      'link': str(item.get('permalink') or '') if str(item.get('permalink') or '').startswith('https://') else ''})
    return saida


PAGINAS_POR_PASSO = 5           # páginas de 100 anúncios lidas por fatia em cada passo (o passo para antes se passar do tempo)
ORCAMENTO_PASSO_S = 15          # o servidor tem limite de 60 s por pedido
LEITORES = 12                   # leituras de anúncios ao mesmo tempo
ESTADOS = ('active', 'paused')  # só ativos e pausados podem ser alterados: encerrados nem são lidos
TIPOS_FATIA = ('gold_special', 'gold_pro')   # Clássico e Premium: a conta é varrida em fatias ao mesmo tempo


def _total(busca, **filtros):
    return int(((busca(dict(filtros, limit=1)) or {}).get('paging') or {}).get('total') or 0)


def _plano(busca):
    """(total, fatias). Cada fatia é uma busca independente (status [+ tipo de anúncio]) que anda ao mesmo tempo que as outras.
    Só divide por tipo se Clássico + Premium somam exatamente o total do status; senão varre o status inteiro (nada fica de fora)."""
    total, fatias = 0, []
    for st in ESTADOS:
        n = _total(busca, status=st)
        total += n
        if not n:
            continue
        por_tipo = [(tp, _total(busca, status=st, listing_type_id=tp)) for tp in TIPOS_FATIA]
        if sum(x for _, x in por_tipo) == n:
            fatias += [{'st': st, 'tp': tp, 's': None, 'f': False} for tp, x in por_tipo if x]
        else:
            fatias.append({'st': st, 'tp': None, 's': None, 'f': False})
    return total, fatias


def _ler_fatias(texto):
    try:
        d = json.loads(texto)
        if isinstance(d, dict) and isinstance(d.get('p'), list) and d['p'] and all(isinstance(x, dict) for x in d['p']):
            return int(d.get('t') or 0), d['p']
    except (TypeError, ValueError):
        pass
    return None, None


def varrer(conta, regra, scroll_id=None):
    """Um passo da varredura: várias fatias da conta andam ao mesmo tempo, com as leituras dos anúncios em paralelo. Só leitura.

    Lê só ativos e pausados (a busca do Mercado Livre filtra por status); se o ML recusar o filtro, volta a ler tudo.
    Devolve {'achados', 'lidos', 'total', 'scroll_id', 'concluido'}. O `scroll_id` (opaco) volta para o próximo passo."""
    tk = fontes.token(conta)
    vendedor = fontes._vendedor(conta, tk)
    inicio = time.monotonic()

    def busca(params):
        try:
            return fontes._ml(tk, '/users/%s/items/search' % vendedor, params)
        except RuntimeError as exc:
            raise fontes.FonteIndisponivel('Não consegui listar os anúncios agora (%s). Tente de novo.' % str(exc)[:80])

    total, fatias = _ler_fatias(scroll_id)
    if fatias is None:                               # primeiro passo
        try:
            total, fatias = _plano(busca)
        except fontes.FonteIndisponivel:
            total, fatias = 0, [{'st': None, 'tp': None, 's': None, 'f': False}]     # o ML não aceitou o filtro: lê tudo, como antes

    def ler(grupo):
        try:
            return fontes._ml(tk, '/items', {'ids': ','.join(grupo), 'attributes': CAMPOS_ITEM})
        except RuntimeError as exc:
            raise fontes.FonteIndisponivel('Não consegui ler os anúncios agora (%s). Tente de novo.' % str(exc)[:80])

    futuros = []

    def andar(f, leitores):
        paginas = 0
        while not f['f'] and paginas < PAGINAS_POR_PASSO and (paginas == 0 or time.monotonic() - inicio < ORCAMENTO_PASSO_S):
            params = {'search_type': 'scan', 'limit': LOTE_VARREDURA}
            if f['st']:
                params['status'] = f['st']
            if f['tp']:
                params['listing_type_id'] = f['tp']
            if f['s']:
                params['scroll_id'] = str(f['s'])[:300]
            pag = busca(params)
            ids = [i for i in (pag or {}).get('results') or [] if fontes._MLB.match(str(i))]
            if not f['st'] and not total:
                nonlocal_total[0] = int(((pag or {}).get('paging') or {}).get('total') or 0)
            if not ids:
                f['f'] = True
                break
            f['s'] = (pag or {}).get('scroll_id') or f['s']
            paginas += 1
            for i in range(0, len(ids), 20):
                futuros.append(leitores.submit(ler, ids[i:i + 20]))

    nonlocal_total = [total]
    pendentes = [f for f in fatias if not f['f']]
    with ThreadPoolExecutor(max_workers=LEITORES) as leitores:
        with ThreadPoolExecutor(max_workers=max(1, len(pendentes))) as andadores:
            for r in [andadores.submit(andar, f, leitores) for f in pendentes]:
                r.result()
        respostas = [f.result() for f in futuros]
    total = nonlocal_total[0]
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
    return {'achados': achou, 'lidos': lidos, 'total': total, 'scroll_id': json.dumps({'t': total, 'p': fatias}), 'concluido': all(f['f'] for f in fatias)}


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


def diagnosticar(conta, mlb):
    """Somente leitura: o que o Mercado Livre guarda de SKU e de fotos num anúncio, e se a varredura enxergou o mesmo."""
    tk = fontes.token(conta)
    completo = fontes._ml(tk, '/items/' + mlb)
    if not isinstance(completo, dict) or str(completo.get('id')) != mlb:
        raise Bloqueio('A API não retornou este anúncio.')
    if str(completo.get('seller_id')) != str(fontes._vendedor(conta, tk)):
        raise Bloqueio('Anúncio não pertence à conta.')
    varredura = None
    try:
        lista = fontes._ml(tk, '/items', {'ids': mlb, 'attributes': CAMPOS_ITEM})
        varredura = (lista[0].get('body') if lista and isinstance(lista[0], dict) else None)
    except (RuntimeError, ValueError):
        pass
    cat = {}
    if completo.get('category_id'):
        try:
            cat = fontes._ml(tk, '/categories/' + str(completo['category_id'])) or {}
        except (RuntimeError, ValueError):
            cat = {}

    def sem(item):
        return None if not isinstance(item, dict) else [l['variacao'] for l in locais_sem_sku(item)]
    vs = []
    for v in completo.get('variations') or []:
        if isinstance(v, dict):
            up_id, up_sku = v.get('user_product_id'), None
            if up_id:                                   # SKU guardado no produto do usuário (modelo novo); só leitura, pode não existir
                try:
                    up = fontes._ml(tk, '/user-products/' + str(up_id))
                    up_sku = _attr_sku(up.get('attributes')) if isinstance(up, dict) else None
                except (RuntimeError, ValueError):
                    up_sku = None
            vs.append({'id': v.get('id'), 'nome': nome_variacao(completo, v.get('id')), 'seller_custom_field': str(v.get('seller_custom_field') or ''),
                       'SELLER_SKU': _attr_sku(v.get('attributes')), 'fotos': len(v.get('picture_ids') or []),
                       'user_product_id': up_id, 'SKU_no_produto_do_usuario': up_sku, 'campos': sorted(v.keys())})
    return {'mlb': mlb, 'status': completo.get('status'), 'categoria': completo.get('category_id'),
            'fotos_do_anuncio': len(completo.get('pictures') or []),
            'limite_fotos_anuncio': cat.get('max_pictures_per_item'), 'limite_fotos_por_variacao': cat.get('max_pictures_per_item_var'),
            'campos_do_anuncio': sorted(completo.keys()), 'sku_do_anuncio': {'seller_custom_field': str(completo.get('seller_custom_field') or ''), 'SELLER_SKU': _attr_sku(completo.get('attributes'))},
            'variacoes': vs, 'sem_sku_lendo_o_anuncio_completo': sem(completo), 'sem_sku_como_a_varredura_enxerga': sem(varredura)}


def _motivo_recusa(texto):
    """Resumo simples da recusa do ML (o JSON cru é longo e cortado)."""
    t = str(texto or '')
    dica = ''
    if 'attributes.duplicated' in t:
        dica = ' (SKU repetido nos campos do anúncio)'
    elif 'variation' in t.lower() and ('duplicate' in t.lower() or 'igual' in t.lower()):
        dica = ' (o anúncio tem variações iguais; é preciso corrigir isso no Mercado Livre)'
    return t[:160] + dica


def trocar_item(conta, tk, vendedor, mlb, alvos, regra):
    """Troca o SKU dos locais `alvos` [(variacao, sku_visto, sku_novo_individual)] de um anúncio. Devolve um dicionário de resultado."""
    r = {'mlb': mlb, 'status': 'PULADO', 'mensagem': '', 'trocas': []}
    try:
        item = fontes._ml(tk, '/items/' + mlb)
        if not isinstance(item, dict) or str(item.get('id')) != mlb:
            raise Bloqueio('A API não retornou este anúncio.')
        if str(item.get('seller_id')) != str(vendedor):
            raise Bloqueio('Anúncio não pertence à conta.')
        if item.get('status') not in ATIVOS:
            raise Bloqueio('Anúncio %s: não dá para alterar.' % (item.get('status') or 'sem status'))
        sem = regra[0] in MODOS_SEM_SKU
        if sem and not titulo_casa(item.get('title'), regra[0], regra[1]):
            raise Bloqueio('O título do anúncio mudou e não casa mais com a busca. Busque de novo.')
        atuais = {l['variacao']: l for l in (locais_sem_sku(item) if sem else locais(item))}
        mudancas, previstos = [], []
        for var, visto, individual in alvos:
            loc = atuais.get(var)
            if loc is None:
                raise Bloqueio('Este anúncio já tem SKU (ou mudou) desde que você viu. Busque de novo.' if sem else 'O SKU deste anúncio mudou desde que você viu. Busque de novo.')
            if not sem and visto not in (loc['scf'], loc['attr']):
                raise Bloqueio('O SKU mudou no Mercado Livre desde que você viu (agora: %s). Busque de novo.' % (loc['scf'] or loc['attr']))
            if regra[0] == 'sem_todos':                 # cada anúncio com o SKU que a pessoa digitou para ele
                novo_ind = str(individual or '').strip()
                if not novo_ind or len(novo_ind) > MAX_SKU or _CTRL.search(novo_ind):
                    raise Bloqueio('SKU novo inválido para este anúncio (máximo %d caracteres).' % MAX_SKU)
                mud = {'scf': novo_ind, 'attr': novo_ind}
            else:
                mud = {'scf': regra[2], 'attr': regra[2]} if sem else _novos_campos(loc, regra)
            if not mud:
                raise Bloqueio('O SKU não casa mais com a busca. Busque de novo.')
            for n in mud.values():
                _valida_novo(n)
            mudancas.append((var, mud))
            previstos.append((var, loc, mud))
        if not reservar(conta, mlb):
            raise Bloqueio('Este anúncio acabou de ser alterado. Aguarde um minuto.')
        resp = fontes._SESS.put(fontes.API + '/items/' + mlb, json=_corpo(mudancas), headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 25))
        if not resp.ok and sem:                  # anúncio sem SKU: se o ML recusar o atributo SELLER_SKU, tenta só o campo de SKU do vendedor
            mudancas = [(v, {'scf': m['scf']}) for v, m in mudancas]
            previstos = [(v, l, {'scf': m['scf']}) for v, l, m in previstos]
            resp = fontes._SESS.put(fontes.API + '/items/' + mlb, json=_corpo(mudancas), headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 25))
        if not resp.ok and not sem and any('attr' in m and 'scf' in m for _v, m in mudancas):
            # SKU que está nos DOIS campos: mandar os dois juntos o ML às vezes recusa ("attributes.duplicated", ele já copia o campo de SKU para o atributo SELLER_SKU).
            # Tenta de novo só com o campo de SKU do vendedor; a conferência abaixo continua exigindo os dois campos com o SKU novo.
            so_campo = [(v, {'scf': m['scf']} if 'scf' in m else m) for v, m in mudancas]
            resp = fontes._SESS.put(fontes.API + '/items/' + mlb, json=_corpo(so_campo), headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 25))
        if not resp.ok:
            r.update(status='ERRO', mensagem='O Mercado Livre recusou a troca (HTTP %s): %s' % (resp.status_code, _motivo_recusa(resp.text)))
            return r
        conf = {l['variacao']: l for l in locais(fontes._ml(tk, '/items/' + mlb))}      # (locais sem SKU não aparecem aqui; um SKU novo aparece)
        tudo_ok = True
        for var, loc, mud in previstos:
            lido = conf.get(var) or {'scf': '', 'attr': ''}
            ok = all(lido[campo] == n for campo, n in mud.items())
            tudo_ok = tudo_ok and ok
            r['trocas'].append({'variacao': var, 'sku_antes': loc['scf'] or loc['attr'] or '(sem SKU)', 'sku_depois': lido['scf'] or lido['attr'], 'conferido': ok})
        if tudo_ok:
            r.update(status='OK', mensagem='SKU trocado e conferido no Mercado Livre.')
        else:
            r.update(status='NAO_CONFIRMADO', mensagem='O Mercado Livre aceitou, mas ao conferir o SKU não ficou como esperado. Abra o anúncio e confira.')
    except Bloqueio as exc:
        r.update(status='PULADO', mensagem=str(exc))
    except (RuntimeError, ValueError) as exc:
        r.update(status='ERRO', mensagem='Falha ao falar com o Mercado Livre: ' + str(exc)[:120])
    return r
