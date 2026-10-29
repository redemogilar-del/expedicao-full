"""Regras do controle de envios para o Full (Mercado Livre).

Entradas:
  - Relatório de Planejamento de envios (xlsx baixado do ML, cabeçalho na linha com "Código ML").
  - Frete (API do Mercado Livre) e custo (planilha de custos) de cada produto, buscados por `fontes.py` e entregues
    aqui como {codigo_ml: {'frete': float|None, 'custo': float|None, 'erro': texto opcional}}.

Regras (as mesmas da planilha de controle):
  - estoque_full = "Aptas para venda"; pendente = "Unidades com recebimento pendente no Full"
  - necessidade = ARREDONDA_PARA_CIMA(vendas 30 dias x meses de estoque - pendente - estoque_full), nunca negativa
  - Produto sem NENHUM estoque no Full (aptas + pendente = 0): envia no mínimo a quantidade de teste (padrão 3)
  - Se o ML sugere uma quantidade menor que o teste (ex.: 1 unidade), arredonda para o teste
  - Frete / custo precisa ser MENOR que o limite (padrão 45%); senão o produto não é enviado
  - Frete ou custo ausente/inválido NUNCA vira zero: o produto fica de fora, com o motivo
"""
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from openpyxl import load_workbook

D = Decimal

PADRAO = {'meses': '1.5', 'limite_frete_pct': '45', 'qtd_teste': '3'}
LIMITES = {'meses': (D('0'), D('12')), 'limite_frete_pct': (D('1'), D('500')), 'qtd_teste': (D('1'), D('50'))}

ST_ENVIAR = 'enviar'
ST_NAO_PRECISA = 'nao_precisa'
ST_FRETE = 'frete_alto'
ST_SEM_DADOS = 'sem_dados'
ST_IMPEDIDO = 'impedido'
ROTULOS = {
    ST_ENVIAR: 'Enviar', ST_NAO_PRECISA: 'Não precisa', ST_FRETE: 'Frete alto',
    ST_SEM_DADOS: 'Sem frete/custo', ST_IMPEDIDO: 'Impedido pelo ML',
}

MAX_LINHAS = 20000

# observações do ML que impedem o envio de estoque
_IMPEDIDOS = (
    ('não pode enviar estoque', 'Restrito pelo ML (peso, tamanho, volume ou preço)'),
    ('dados são necessários', 'Faltam dados fiscais para oferecer o Full'),
    ('era igual a outro anúncio', 'Era igual a outro anúncio (usar o anúncio ativo)'),
)


class ArquivoInvalido(ValueError):
    """Mensagem pensada para aparecer na tela para quem usa."""


def _norm(t):
    t = unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', t).strip()


def _numero(v):
    """Decimal ou None. Aceita 1.234,56 e 1234.56; '-', 'N/A', texto => None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return D(str(v)) if math.isfinite(v) else None
    t = str(v).strip().replace('R$', '').replace('\xa0', '').replace(' ', '')
    if not t or t in ('-', '—') or not re.fullmatch(r'-?[\d.,]+', t):
        return None
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        n = D(t)
    except InvalidOperation:
        return None
    return n if n.is_finite() else None


def _mlb(t):
    t = t.strip()
    return 'MLB' + t if t.isdigit() else t


def _inteiro(v, padrao=0):
    n = _numero(v)
    return int(n) if n is not None else padrao


def _txt(v):
    return '' if v is None else str(v).strip()


def validar_parametros(bruto):
    """Devolve (Decimal meses, Decimal limite_pct, int qtd_teste) ou levanta ArquivoInvalido."""
    bruto = bruto or {}
    saida = {}
    nomes = {'meses': 'Meses de estoque', 'limite_frete_pct': 'Limite de frete (%)', 'qtd_teste': 'Unidades do envio-teste'}
    for k, padrao in PADRAO.items():
        v = _numero(bruto.get(k) if bruto.get(k) not in (None, '') else padrao)
        lo, hi = LIMITES[k]
        if v is None or v < lo or v > hi:
            raise ArquivoInvalido('%s deve ficar entre %s e %s.' % (nomes[k], lo, hi))
        saida[k] = v
    saida['qtd_teste'] = int(saida['qtd_teste'])
    return saida


def _abrir(arquivo):
    try:
        return load_workbook(arquivo, read_only=True, data_only=True)
    except Exception:
        raise ArquivoInvalido('Não consegui abrir o arquivo. Envie a planilha Excel (.xlsx) original.')


def _achar_cabecalho(ws, colunas, necessarias, max_linhas=30):
    """Procura a linha de cabeçalho; devolve (indice_linha, {campo: indice_coluna})."""
    for i, linha in enumerate(ws.iter_rows(min_row=1, max_row=max_linhas, values_only=True)):
        cab = [_norm(str(c).split('\n')[0]) if c is not None else '' for c in linha]
        achados = {}
        for campo, prefixos in colunas.items():
            for j, c in enumerate(cab):
                if c and any(c == p or c.startswith(p) for p in prefixos):
                    achados.setdefault(campo, j)
                    break
        if all(c in achados for c in necessarias):
            return i + 1, achados
    return None, None


_COLS_RELATORIO = {
    'codigo_ml': ['codigo ml'], 'sku': ['sku'], 'produto': ['produto'], 'mlbu': ['numero de produto'],
    'anuncios': ['anuncios com este produto'], 'tamanho': ['tamanho'],
    'vendas': ['unidades vendidas'], 'aptas': ['aptas para'], 'pendentes': ['unidades com recebimento pendente'],
    'sugeridas': ['unidades sugeridas'], 'prazo': ['prazo limite'], 'urgencia': ['urgencia'], 'obs': ['observacoes'],
    'estrela': ['produto estrela'], 'variacao': ['variacao'],
}
_OBRIG_RELATORIO = ('mlbu', 'sku', 'vendas', 'aptas', 'pendentes')


def ler_relatorio(arquivo):
    wb = _abrir(arquivo)
    for ws in wb.worksheets:
        linha_cab, col = _achar_cabecalho(ws, _COLS_RELATORIO, _OBRIG_RELATORIO)
        if col is not None:
            break
    else:
        raise ArquivoInvalido('Este não parece o "Relatório de Planejamento de envios" do Mercado Livre '
                              '(não achei as colunas Número de produto, SKU, Unidades vendidas, Aptas para venda e Recebimento pendente).')
    itens, vistos = [], set()
    for linha in ws.iter_rows(min_row=linha_cab + 1, values_only=True):
        def g(campo):
            j = col.get(campo)
            return linha[j] if j is not None and j < len(linha) else None
        mlbu, sku, cod = _txt(g('mlbu')), _txt(g('sku')), _txt(g('codigo_ml'))
        if mlbu in ('0', '-', 'N/A'):
            mlbu = ''      # anúncio sem número de produto (não é de catálogo)
        if not (mlbu or sku or cod):
            continue
        if len(itens) >= MAX_LINHAS:
            raise ArquivoInvalido('Arquivo grande demais (mais de %d produtos).' % MAX_LINHAS)
        chave = cod or mlbu or ('SKU:' + sku)
        if chave in vistos:      # o relatório não repete produto; se repetir, soma estaria errado
            raise ArquivoInvalido('O produto %s aparece mais de uma vez no relatório.' % chave)
        vistos.add(chave)
        prazo = g('prazo')
        itens.append({
            'codigo_ml': _txt(g('codigo_ml')), 'mlbu': mlbu, 'sku': sku, 'produto': _txt(g('produto')),
            'mlbs': [_mlb(m) for m in _txt(g('anuncios')).split(',') if m.strip()],
            'tamanho': _txt(g('tamanho')),
            'vendas': _inteiro(g('vendas')), 'aptas': _inteiro(g('aptas')), 'pendentes': _inteiro(g('pendentes')),
            'sugeridas': _inteiro(g('sugeridas'), None) if _numero(g('sugeridas')) is not None else None,
            'prazo': prazo.strftime('%d/%m') if hasattr(prazo, 'strftime') else _txt(prazo),
            'urgencia': _txt(g('urgencia')), 'obs': _txt(g('obs')),
            'estrela': _txt(g('estrela')),
        })
    if not itens:
        raise ArquivoInvalido('O relatório não tem nenhum produto.')
    return itens


def _br(v, casas=2):
    """Número no formato brasileiro (vírgula decimal)."""
    return ('{:,.%df}' % casas).format(float(v)).replace(',', 'X').replace('.', ',').replace('X', '.')


def _impedimento(obs):
    n = _norm(obs)
    for trecho, motivo in _IMPEDIDOS:
        if _norm(trecho) in n:
            return motivo
    return None


def calcular_qtd(item, params):
    """(quantidade, tipo) pela regra da planilha de controle; tipo = 'Reposição' | 'Teste' | ''."""
    meses, teste = params['meses'], params['qtd_teste']
    vendas, aptas, pend, sug = item['vendas'], item['aptas'], item['pendentes'], item['sugeridas']
    total = aptas + pend
    necessidade = (D(vendas) * meses - D(pend) - D(aptas)).to_integral_value(rounding=ROUND_CEILING)
    qtd, tipo = max(0, int(necessidade)), 'Reposição'
    if sug is not None and sug > qtd:
        qtd = sug
    if vendas == 0:
        qtd, tipo = max(qtd, teste), 'Teste'        # sem venda nos últimos 30 dias = anúncio de teste
    elif total == 0 and qtd < teste:
        qtd, tipo = teste, 'Teste'
    elif sug is not None and 0 < sug < teste:
        qtd, tipo = max(qtd, teste), 'Teste'
    return qtd, (tipo if qtd else '')


def pendencias(itens, params):
    """Produtos para os quais vale buscar frete e custo: precisam de envio e o ML não impede."""
    saida = []
    for i in itens:
        qtd, _ = calcular_qtd(i, params)
        if qtd > 0 and not _impedimento(i['obs']):
            saida.append({'codigo_ml': i['codigo_ml'], 'sku': i['sku'], 'mlbs': i['mlbs']})
    return saida


def decidir(item, base, params):
    """Devolve o item enriquecido com qtd, tipo, status e motivo."""
    meses, limite, teste = params['meses'], params['limite_frete_pct'], params['qtd_teste']
    vendas, aptas, pend = item['vendas'], item['aptas'], item['pendentes']
    total = aptas + pend
    qtd, tipo = calcular_qtd(item, params)

    reg = dict(item)
    b = (base or {}).get(item['codigo_ml']) or {}
    frete, custo = b.get('frete'), b.get('custo')
    impacto = None
    if frete is not None and custo is not None and custo > 0 and frete >= 0:
        impacto = float(D(str(frete)) / D(str(custo)) * 100)
    reg.update(estoque_total=total, frete=frete, custo=custo, impacto_pct=None if impacto is None else round(impacto, 1),
               qtd=qtd, tipo=tipo if qtd else '', cobertura_dias=(round((aptas + pend) / vendas * 30) if vendas else None))

    if qtd == 0:
        return dict(reg, status=ST_NAO_PRECISA, qtd=0,
                    motivo='Estoque no Full já cobre %s meses de venda' % (str(meses).rstrip('0').rstrip('.') if '.' in str(meses) else meses))
    imp = _impedimento(item['obs'])
    if imp:
        return dict(reg, status=ST_IMPEDIDO, motivo=imp)
    if not b:
        return dict(reg, status=ST_SEM_DADOS, motivo='Frete e custo não foram buscados para este produto')
    if impacto is None:
        faltam = [n for n, v in (('frete', frete), ('custo', custo)) if v is None or (n == 'custo' and v <= 0)]
        detalhe = ' (%s)' % b['erro'] if b.get('erro') else ''
        return dict(reg, status=ST_SEM_DADOS, motivo='Sem %s válido%s' % (' e '.join(faltam or ['frete/custo']), detalhe))
    if D(str(impacto)) >= limite:
        return dict(reg, status=ST_FRETE, motivo='Frete de R$ %s é %s%% do custo (limite %s%%)' % (_br(frete), _br(impacto, 1), _br(limite, 0)))
    motivo = ('Sem estoque no Full: envio-teste de %d un.' % teste if tipo == 'Teste'
              else 'Cobrir %s meses de venda (%d vendidas em 30 dias)' % (str(meses).replace('.', ','), vendas))
    return dict(reg, status=ST_ENVIAR, motivo=motivo)


def calcular(itens, base, params):
    linhas = [decidir(i, base, params) for i in itens]
    ordem = {ST_ENVIAR: 0, ST_FRETE: 1, ST_SEM_DADOS: 2, ST_IMPEDIDO: 3, ST_NAO_PRECISA: 4}
    linhas.sort(key=lambda r: (ordem[r['status']], r['sku'], -r['qtd']))
    return linhas


def resumo_sku(linhas):
    """Total por SKU só do que vai ser enviado. `linhas`: itens com 'sku' e 'qtd' (já filtrados)."""
    tot = {}
    for r in linhas:
        t = tot.setdefault(r['sku'] or '(sem SKU)', {'sku': r['sku'] or '(sem SKU)', 'qtd': 0, 'produtos': 0})
        t['qtd'] += int(r['qtd'])
        t['produtos'] += 1
    return sorted(tot.values(), key=lambda t: t['sku'])


def contagens(linhas):
    c = {k: 0 for k in ROTULOS}
    un = 0
    for r in linhas:
        c[r['status']] += 1
        if r['status'] == ST_ENVIAR:
            un += r['qtd']
    return {'por_status': c, 'unidades_enviar': un, 'skus_enviar': len({r['sku'] for r in linhas if r['status'] == ST_ENVIAR})}
