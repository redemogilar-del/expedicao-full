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


def trocar_sku_item(conta, tk, vendedor, mlb, sku_atual, sku_novo):
    """Troca o SKU de UM anúncio (todas as variações que têm `sku_atual`; se `sku_atual` vazio, as que estão sem SKU).
    Usa a mesma troca conferida da aba Trocar SKU (`skus.trocar_item`)."""
    sku_atual, sku_novo = str(sku_atual or '').strip(), str(sku_novo or '').strip()
    try:
        if not sku_novo or len(sku_novo) > skus.MAX_SKU or skus._CTRL.search(sku_novo):
            raise Bloqueio('Digite o SKU novo (até %d caracteres).' % skus.MAX_SKU)
        if sku_novo.casefold() == sku_atual.casefold() and sku_novo == sku_atual:
            raise Bloqueio('O SKU novo é igual ao atual.')
        item = _ler(tk, vendedor, mlb)
        if item.get('status') not in skus.ATIVOS:
            raise Bloqueio('Anúncio %s: não dá para alterar.' % (item.get('status') or 'sem status'))
        if sku_atual:
            regra = ('igual', sku_atual, sku_novo, 'inteiro')
            alvos = [(l['variacao'], l['scf'] if l['scf'].casefold() == sku_atual.casefold() else l['attr'], '') for l in skus.locais(item)
                     if sku_atual.casefold() in (l['scf'].casefold(), l['attr'].casefold())]
        else:
            regra = ('sem_todos', '', '', 'inteiro')
            alvos = [(l['variacao'], '', sku_novo) for l in skus.locais_sem_sku(item)]
        if not alvos:
            raise Bloqueio('Não achei o SKU "%s" neste anúncio (ele mudou?). Busque de novo.' % sku_atual if sku_atual else 'Este anúncio já tem SKU. Busque de novo.')
    except Bloqueio as exc:
        return {'mlb': mlb, 'status': 'PULADO', 'mensagem': str(exc), 'trocas': []}
    except (RuntimeError, ValueError) as exc:
        return {'mlb': mlb, 'status': 'ERRO', 'mensagem': 'Falha ao falar com o Mercado Livre: ' + str(exc)[:120], 'trocas': []}
    try:
        return skus.trocar_item(conta, tk, vendedor, mlb, alvos, regra)
    except Exception as exc:                   # trocar_item já trata os erros comuns; isto é só a rede de segurança
        return {'mlb': mlb, 'status': 'ERRO', 'mensagem': str(exc)[:160], 'trocas': []}
