"""Ações manuais sobre anúncios, a partir da tela Vendas do dia: pausar e trocar SKU de um anúncio.

Regras (iguais às de preço/SKU): só altera com seleção + confirmação, lê o anúncio de novo na hora,
confere no Mercado Livre depois de gravar e nunca dá como feito sem conferência.
"""
import threading
import time

import fontes
import skus

CONFIRMACAO_PAUSAR = 'PAUSAR_ANUNCIOS'
MAX_PAUSAR = 10
JANELA = 60
_RECENTES, _LOCK = {}, threading.Lock()


class Bloqueio(Exception):
    """Motivo, em português simples, pelo qual o anúncio não foi alterado."""


def _reservar(chave):
    agora = time.monotonic()
    with _LOCK:
        for k in [k for k, t in _RECENTES.items() if agora - t > JANELA]:
            _RECENTES.pop(k, None)
        if chave in _RECENTES:
            return False
        _RECENTES[chave] = agora
        return True


def _ler(tk, vendedor, mlb):
    item = fontes._ml(tk, '/items/' + mlb)
    if not isinstance(item, dict) or str(item.get('id')) != mlb:
        raise Bloqueio('A API não retornou este anúncio.')
    if str(item.get('seller_id')) != str(vendedor):
        raise Bloqueio('Anúncio não pertence à conta.')
    return item


def pausar_item(conta, tk, vendedor, mlb):
    """Pausa um anúncio ATIVO. Devolve {'mlb','status','mensagem'} com status OK / PULADO / ERRO / NAO_CONFIRMADO."""
    r = {'mlb': mlb, 'status': 'PULADO', 'mensagem': ''}
    try:
        item = _ler(tk, vendedor, mlb)
        st = item.get('status')
        if st == 'paused':
            r['mensagem'] = 'Já estava pausado.'
            return r
        if st != 'active':
            raise Bloqueio('Só dá para pausar anúncio ativo (este está: %s).' % (st or 'sem status'))
        if not _reservar((conta, mlb)):
            raise Bloqueio('Este anúncio acabou de ser alterado. Aguarde um minuto.')
        resp = fontes._SESS.put(fontes.API + '/items/' + mlb, json={'status': 'paused'}, headers={'Authorization': 'Bearer ' + tk}, timeout=(5, 25))
        if not resp.ok:
            r.update(status='ERRO', mensagem='O Mercado Livre recusou (HTTP %s): %s' % (resp.status_code, resp.text[:160]))
            return r
        depois = _ler(tk, vendedor, mlb)
        if depois.get('status') == 'paused':
            r.update(status='OK', mensagem='Anúncio pausado e conferido no Mercado Livre.')
        else:
            r.update(status='NAO_CONFIRMADO', mensagem='O Mercado Livre aceitou, mas o anúncio ainda aparece como %s. Confira no Mercado Livre.' % (depois.get('status') or 'sem status'))
    except Bloqueio as exc:
        r['mensagem'] = str(exc)
    except (RuntimeError, ValueError) as exc:
        r.update(status='ERRO', mensagem='Falha ao falar com o Mercado Livre: ' + str(exc)[:120])
    return r


_QUALQUER = object()
_GRAVIDADE = {'OK': 0, 'PULADO': 1, 'NAO_CONFIRMADO': 2, 'ERRO': 3}


def skus_do_anuncio(tk, vendedor, mlb):
    """Somente leitura: onde há SKU no anúncio — ele mesmo ou CADA variação (com ou sem SKU)."""
    item = _ler(tk, vendedor, mlb)
    variacoes = [v for v in item.get('variations') or [] if isinstance(v, dict) and v.get('id') is not None]
    if variacoes:
        locais = [{'variacao': v['id'], 'nome': skus.nome_variacao(item, v['id']), 'sku': str(v.get('seller_custom_field') or '').strip() or skus._attr_sku(v.get('attributes'))} for v in variacoes]
    else:
        locais = [{'variacao': None, 'nome': '', 'sku': str(item.get('seller_custom_field') or '').strip() or skus._attr_sku(item.get('attributes'))}]
    return {'mlb': mlb, 'titulo': str(item.get('title') or '')[:120], 'status': str(item.get('status') or ''), 'locais': locais}


def _trocar_um(conta, tk, vendedor, mlb, sku_atual, sku_novo, variacao=_QUALQUER):
    """Troca UM SKU de um anúncio (da variação `variacao`, ou de todos os locais que têm `sku_atual`; sem `sku_atual`, os locais sem SKU).
    Usa a mesma troca conferida da aba Trocar SKU (`skus.trocar_item`)."""
    sku_atual, sku_novo = str(sku_atual or '').strip(), str(sku_novo or '').strip()
    try:
        if not sku_novo or len(sku_novo) > skus.MAX_SKU or skus._CTRL.search(sku_novo):
            raise Bloqueio('Digite o SKU novo (até %d caracteres).' % skus.MAX_SKU)
        if sku_novo == sku_atual:
            raise Bloqueio('O SKU novo é igual ao atual.')
        item = _ler(tk, vendedor, mlb)
        if item.get('status') not in skus.ATIVOS:
            raise Bloqueio('Anúncio %s: não dá para alterar.' % (item.get('status') or 'sem status'))
        if sku_atual:
            regra = ('igual', sku_atual, sku_novo, 'inteiro')
            alvos = [(l['variacao'], l['scf'] if l['scf'].casefold() == sku_atual.casefold() else l['attr'], '') for l in skus.locais(item)
                     if sku_atual.casefold() in (l['scf'].casefold(), l['attr'].casefold()) and (variacao is _QUALQUER or l['variacao'] == variacao)]
        else:
            regra = ('sem_todos', '', '', 'inteiro')
            alvos = [(l['variacao'], '', sku_novo) for l in skus.locais_sem_sku(item) if variacao is _QUALQUER or l['variacao'] == variacao]
        if not alvos:
            raise Bloqueio('Não achei o SKU "%s" neste anúncio (ele mudou?). Abra o painel de novo.' % sku_atual if sku_atual else 'Este local já tem SKU. Abra o painel de novo.')
    except Bloqueio as exc:
        return {'mlb': mlb, 'status': 'PULADO', 'mensagem': str(exc), 'trocas': []}
    except (RuntimeError, ValueError) as exc:
        return {'mlb': mlb, 'status': 'ERRO', 'mensagem': 'Falha ao falar com o Mercado Livre: ' + str(exc)[:120], 'trocas': []}
    try:
        return skus.trocar_item(conta, tk, vendedor, mlb, alvos, regra)
    except Exception as exc:                   # trocar_item já trata os erros comuns; isto é só a rede de segurança
        return {'mlb': mlb, 'status': 'ERRO', 'mensagem': str(exc)[:160], 'trocas': []}


def trocar_sku_item(conta, tk, vendedor, mlb, trocas):
    """`trocas`: [{'variacao': id|None (opcional), 'sku_atual', 'sku_novo'}] de UM anúncio (cada variação pode ter um SKU novo diferente).
    Faz uma troca conferida por vez e junta o resultado (vale o pior)."""
    if not isinstance(trocas, list) or not trocas or len(trocas) > 60:
        return {'mlb': mlb, 'status': 'PULADO', 'mensagem': 'Nada para trocar.', 'trocas': []}
    vistos, partes, todas = set(), [], []
    for t in trocas:
        t = t if isinstance(t, dict) else {}
        var = t.get('variacao') if 'variacao' in t else _QUALQUER
        if var is not _QUALQUER and not (var is None or (isinstance(var, int) and not isinstance(var, bool))):
            return {'mlb': mlb, 'status': 'PULADO', 'mensagem': 'Variação inválida.', 'trocas': []}
        if var in vistos and var is not _QUALQUER:
            return {'mlb': mlb, 'status': 'PULADO', 'mensagem': 'Variação repetida.', 'trocas': []}
        vistos.add(var)
        r = _trocar_um(conta, tk, vendedor, mlb, t.get('sku_atual'), t.get('sku_novo'), var)
        with skus._LOCK:                       # o clique duplo continua barrado entre pedidos; dentro deste, a próxima variação pode seguir
            skus._RECENTES.pop((conta, mlb), None)
        partes.append(r)
        todas.extend(r.get('trocas') or [])
    pior = max(partes, key=lambda r: _GRAVIDADE.get(r['status'], 3))
    if len(partes) == 1:
        return partes[0]
    ok = sum(r['status'] == 'OK' for r in partes)
    msgs = '; '.join(r['mensagem'] for r in partes if r['status'] != 'OK')
    return {'mlb': mlb, 'status': pior['status'] if ok != len(partes) else 'OK', 'trocas': todas,
            'mensagem': ('%d de %d trocas conferidas.' % (ok, len(partes))) + (' ' + msgs if msgs else '')}
