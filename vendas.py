"""Regra de lucro das vendas (aba Vendas do dia). Sem Flask.

  repasse = venda − tarifa do ML − frete do vendedor      ("o que sobrou da venda")
  imposto = venda × imposto%                              (imposto sobre o valor final da venda)
  lucro   = repasse − imposto − custo × quantidade
  margem  = lucro ÷ (custo × quantidade) × 100            (margem sobre o custo, como na planilha)

Dado ausente (custo, tarifa, frete) NUNCA vira zero: a linha fica "Sem dados" e não entra na conta.
"""
from decimal import ROUND_HALF_UP, Decimal

PADRAO = {'imposto_pct': '', 'margem_padrao': '20'}      # imposto vazio = usa o da planilha (aba TaxasML) por conta e modalidade
CLASSES = ('prejuizo', 'abaixo', 'dentro', 'sem_dados')
ROTULOS = {'prejuizo': 'Prejuízo', 'abaixo': 'Abaixo da margem', 'dentro': 'Dentro da margem', 'sem_dados': 'Sem dados'}


class ParametroInvalido(ValueError):
    pass


def D(v):
    return Decimal(str(v))


def _dois(v):
    return float(D(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _decimal(bruto, minimo, maximo, nome):
    try:
        v = D(str(bruto).replace(',', '.'))
    except Exception:
        raise ParametroInvalido('%s: digite um número.' % nome)
    if not v.is_finite() or not minimo <= v <= maximo:
        raise ParametroInvalido('%s: use um valor entre %d e %d.' % (nome, minimo, maximo))
    return v


def validar_parametros(brutos):
    """{'imposto_pct': Decimal|None (None = usa a planilha), 'margem_padrao': Decimal, 'impostos': {conta: {'normal': Decimal, 'full': Decimal}}}."""
    brutos = brutos or {}
    saida = {'margem_padrao': _decimal(brutos.get('margem_padrao', PADRAO['margem_padrao']), 0, 1000, 'Margem mínima padrão')}
    imp = brutos.get('imposto_pct', PADRAO['imposto_pct'])
    saida['imposto_pct'] = None if imp is None or str(imp).strip() == '' else _decimal(imp, 0, 60, 'Imposto')
    saida['impostos'] = {}
    tabela = brutos.get('impostos') or {}
    if not isinstance(tabela, dict):
        raise ParametroInvalido('Impostos da planilha inválidos.')
    for conta, d in tabela.items():
        if isinstance(d, dict) and str(conta) in ('ML19', 'ML44') and d.get('normal') is not None and d.get('full') is not None:
            saida['impostos'][str(conta)] = {'normal': _decimal(d['normal'], 0, 60, 'Imposto da planilha'), 'full': _decimal(d['full'], 0, 60, 'Imposto da planilha')}
    return saida


def imposto_da_linha(linha, params):
    """% de imposto da venda: o digitado na tela (vale para tudo) ou o da planilha (TaxasML) da conta, Full ou normal. None = indisponível."""
    if params.get('imposto_pct') is not None:
        return params['imposto_pct']
    return ((params.get('impostos') or {}).get(str(linha.get('conta') or '')) or {}).get('full' if linha.get('grupo') == 'full' else 'normal')


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float('inf') else None


def analisar(linha, cad, params):
    """Devolve a linha enriquecida com custo, imposto, lucro, margem, meta e classe."""
    reg = dict(linha)
    qtd = int(_num(linha.get('quantidade')) or 0)
    venda, tarifa, frete = _num(linha.get('venda')), _num(linha.get('tarifa')), _num(linha.get('frete'))
    cad = cad or {}
    custo = _num(cad.get('custo'))
    full = linha.get('grupo') == 'full'
    meta, origem = _num(cad.get('margem_full') if full else cad.get('margem')), 'planilha'
    if meta is None:
        meta, origem = float(params['margem_padrao']), 'padrão'
    faltam = []
    if custo is None or custo <= 0:
        faltam.append('custo na planilha' if str(linha.get('sku') or '').strip() else 'SKU do anúncio (ou digite o custo)')
    if venda is None or venda <= 0 or qtd <= 0:
        faltam.append('valor da venda')
    if tarifa is None:
        faltam.append('tarifa do ML')
    if frete is None:
        faltam.append('frete')
    imp_pct = imposto_da_linha(linha, params)
    if imp_pct is None:
        faltam.append('imposto (aba TaxasML ou digite na tela)')
    reg.update({'custo_unit': custo if custo and custo > 0 else None, 'meta': meta, 'meta_origem': origem,
                'repasse': None, 'imposto': None, 'imposto_pct': float(imp_pct) if imp_pct is not None else None, 'custo_total': None, 'lucro': None, 'margem': None, 'classe': 'sem_dados', 'perda': None, 'perda_tipo': '',
                'pendencias': ', '.join(faltam)})
    if faltam:
        return reg
    repasse = D(venda) - D(tarifa) - D(frete)
    imposto = D(venda) * imp_pct / 100
    custo_total = D(custo) * qtd
    lucro = repasse - imposto - custo_total
    margem = lucro / custo_total * 100
    reg.update({'repasse': _dois(repasse), 'imposto': _dois(imposto), 'custo_total': _dois(custo_total), 'lucro': _dois(lucro), 'margem': _dois(margem)})
    reg['classe'] = 'prejuizo' if lucro < 0 else ('abaixo' if margem < D(str(meta)) else 'dentro')
    # perda em dinheiro: prejuízo (lucro negativo) ou o que deixou de ganhar para chegar na margem mínima
    if reg['classe'] == 'prejuizo':
        reg['perda'], reg['perda_tipo'] = _dois(-lucro), 'prejuizo'
    elif reg['classe'] == 'abaixo':
        reg['perda'], reg['perda_tipo'] = _dois(custo_total * D(str(meta)) / 100 - lucro), 'deixou'
    else:
        reg['perda'] = 0.0
    return reg


def calcular(linhas, cadastro, params):
    """(linhas analisadas, resumo). O cadastro vem de fontes.cadastro: {sku: {custo, margem, margem_full}}."""
    saida = [analisar(l, cadastro.get(str(l.get('sku') or '').strip()) or cadastro.get(str(l.get('mlb') or '').strip()), params) for l in linhas]   # sem SKU: custo/margem digitados na tela vêm pelo MLB
    return saida, resumo(saida)


def resumo(linhas):
    r = {c: 0 for c in CLASSES}
    r.update({'linhas': len(linhas), 'venda': 0.0, 'lucro': 0.0, 'unidades': 0, 'prejuizo_rs': 0.0, 'deixou_rs': 0.0})
    for l in linhas:
        r[l['classe']] += 1
        r['unidades'] += int(l.get('quantidade') or 0)
        if l['classe'] != 'sem_dados':
            r['venda'] += l['venda']; r['lucro'] += l['lucro']
            if l['perda_tipo'] == 'prejuizo':
                r['prejuizo_rs'] += l['perda']
            elif l['perda_tipo'] == 'deixou':
                r['deixou_rs'] += l['perda']
    r['venda'], r['lucro'], r['prejuizo_rs'], r['deixou_rs'] = _dois(r['venda']), _dois(r['lucro']), _dois(r['prejuizo_rs']), _dois(r['deixou_rs'])
    return r
