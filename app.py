"""Expedição Mogilar — painel de rotina de envios e vendas.

Primeira aba: Envios Full (planejamento de envios ao Fulfillment do Mercado Livre).
Todo o processamento é feito a partir dos arquivos enviados pela pessoa; nada é gravado no servidor
e nada é alterado no Mercado Livre.
"""
import json
import logging
import os
from datetime import datetime
from io import BytesIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash

import envios
import fontes
import precos
import vendas

log = logging.getLogger('expedicao')
FUSO = ZoneInfo('America/Sao_Paulo')

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get('EXP_SECRET_KEY') or None,
    MAX_CONTENT_LENGTH=12 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=bool(os.environ.get('RENDER')),
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    JSON_AS_ASCII=False,
)
USUARIO = os.environ.get('EXP_LOGIN_USER', '')
SENHA_HASH = os.environ.get('EXP_LOGIN_PASSWORD_HASH', '')
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
ABAS = [('envios', 'Envios Full', True), ('candidatos', 'Candidatos ao Full', True), ('vendas', 'Vendas do dia', True)]


def _configurado():
    return bool(app.config['SECRET_KEY'] and USUARIO and SENHA_HASH)


@app.after_request
def _cabecalhos(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'same-origin')
    resp.headers.setdefault('Content-Security-Policy',
                            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
    if request.path.startswith('/api/') or request.path in ('/login', '/envios'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp


def _mesma_origem():
    origem = request.headers.get('Origin')
    if not origem:
        return True
    return urlparse(origem).netloc == request.host


@app.before_request
def _exigir_login():
    if request.endpoint in ('saude', 'static'):
        return None
    if not _configurado():
        return ('Painel ainda não configurado: defina EXP_SECRET_KEY, EXP_LOGIN_USER e EXP_LOGIN_PASSWORD_HASH.', 503)
    if request.method == 'POST' and not _mesma_origem():
        return jsonify(ok=False, error='Origem não permitida'), 403
    if request.endpoint in ('login', 'sair'):
        return None
    if not session.get('ok'):
        if request.path.startswith('/api/'):
            return jsonify(ok=False, error='Sessão expirada. Entre de novo.'), 401
        return redirect(url_for('login'))
    return None


@app.get('/saude')
def saude():
    return 'ok'


@app.route('/login', methods=['GET', 'POST'])
def login():
    erro = ''
    if request.method == 'POST':
        usuario = request.form.get('usuario', '')
        senha = request.form.get('senha', '')
        if usuario == USUARIO and check_password_hash(SENHA_HASH, senha):
            session.clear()
            session['ok'] = True
            session.permanent = True
            return redirect(url_for('envios_pagina'))
        erro = 'Usuário ou senha incorretos.'
    return render_template('login.html', erro=erro), (401 if erro else 200)


@app.get('/sair')
def sair():
    session.clear()
    return redirect(url_for('login'))


@app.get('/')
def inicio():
    return redirect(url_for('envios_pagina'))


@app.get('/envios')
def envios_pagina():
    return render_template('envios.html', abas=ABAS, ativa='envios', padrao=envios.PADRAO)


@app.get('/candidatos')
def candidatos_pagina():
    return render_template('candidatos.html', abas=ABAS, ativa='candidatos')


@app.get('/vendas')
def vendas_pagina():
    return render_template('vendas.html', abas=ABAS, ativa='vendas', padrao=vendas.PADRAO, hoje=datetime.now(FUSO).strftime('%Y-%m-%d'))


def _erro(exc):
    if isinstance(exc, envios.ArquivoInvalido):
        return jsonify(ok=False, error=str(exc)), 400
    log.exception('Falha inesperada')
    return jsonify(ok=False, error='Algo deu errado ao processar. Confira o arquivo e tente de novo.'), 500


def _arquivo(campo):
    f = request.files.get(campo)
    if not f or not f.filename:
        raise envios.ArquivoInvalido('Escolha o arquivo.')
    if not f.filename.lower().endswith(('.xlsx', '.xlsm')):
        raise envios.ArquivoInvalido('O arquivo precisa ser Excel (.xlsx). Se for do Google Planilhas: Arquivo > Fazer download > Excel.')
    dados = f.read()
    if not dados.startswith(b'PK'):
        raise envios.ArquivoInvalido('Este arquivo não é um Excel (.xlsx) válido.')
    return BytesIO(dados), f.filename


def _params_form():
    return envios.validar_parametros({k: request.form.get(k) for k in envios.PADRAO})


def _erro_fonte(exc):
    if isinstance(exc, fontes.FonteIndisponivel):
        return jsonify(ok=False, error=str(exc)), 503
    return _erro(exc)


@app.post('/api/envios/ler')
def api_ler():
    """Lê o relatório e diz de quais produtos vale buscar frete e custo (só os que precisam de envio)."""
    try:
        arq, nome = _arquivo('relatorio')
        params = _params_form()
        itens = envios.ler_relatorio(arq)
        pend = envios.pendencias(itens, params)
        return jsonify(ok=True, arquivo=nome, produtos=len(itens), pendencias=pend,
                       skus=sorted({p['sku'] for p in pend if p['sku']}))
    except Exception as exc:
        return _erro(exc)


@app.post('/api/envios/custos')
def api_custos():
    try:
        corpo = request.get_json(silent=True) or {}
        skus = corpo.get('skus')
        if not isinstance(skus, list) or not 1 <= len(skus) <= 200 or not all(isinstance(x, str) and 0 < len(x) <= 80 for x in skus):
            raise envios.ArquivoInvalido('Lista de SKUs inválida.')
        return jsonify(ok=True, custos=fontes.custos(skus))
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/envios/aptos')
def api_aptos():
    try:
        return jsonify(ok=True, skus=fontes.aptos_full())
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/envios/fretes')
def api_fretes():
    try:
        corpo = request.get_json(silent=True) or {}
        conta = corpo.get('conta')
        itens = corpo.get('itens')
        if conta not in fontes.CONTAS:
            raise envios.ArquivoInvalido('Escolha a conta do Mercado Livre.')
        if not isinstance(itens, list) or not 1 <= len(itens) <= 10:
            raise envios.ArquivoInvalido('Envie de 1 a 10 produtos por vez.')
        limpos = []
        for i in itens:
            cod = str((i or {}).get('codigo_ml') or '').strip() if isinstance(i, dict) else ''
            mlbs = [str(m).strip() for m in (i.get('mlbs') or [])[:5]] if isinstance(i, dict) else []
            if not cod or len(cod) > 40 or not all(m.startswith('MLB') and m[3:].isdigit() for m in mlbs):
                raise envios.ArquivoInvalido('Produto inválido na lista.')
            limpos.append({'codigo_ml': cod, 'mlbs': mlbs})
        resultados, pendentes = fontes.fretes(conta, limpos)
        return jsonify(ok=True, fretes=resultados, pendentes=pendentes)
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/envios/calcular')
def api_calcular():
    try:
        arq, nome = _arquivo('relatorio')
        params = _params_form()
        try:
            base = json.loads(request.form.get('base') or '{}')
        except ValueError:
            raise envios.ArquivoInvalido('Frete e custo inválidos. Calcule de novo.')
        if not isinstance(base, dict):
            raise envios.ArquivoInvalido('Frete e custo inválidos. Calcule de novo.')
        itens = envios.ler_relatorio(arq)
        linhas = envios.calcular(itens, base, params)
        return jsonify(ok=True, arquivo=nome, linhas=linhas, contagens=envios.contagens(linhas),
                       parametros={k: str(v) for k, v in params.items()})
    except Exception as exc:
        return _erro(exc)


def _seguro(v):
    """Evita que texto vindo de planilhas seja lido como fórmula no Excel (começa com = + - @)."""
    t = '' if v is None else str(v)
    return "'" + t if t[:1] in ('=', '+', '@') else t


def _sem_traco(v):
    t = str(v or '').strip()
    return '' if t == '-' else t


def _linhas_validas(bruto):
    if not isinstance(bruto, list) or not bruto or len(bruto) > envios.MAX_LINHAS:
        raise envios.ArquivoInvalido('Nada para exportar.')
    saida = []
    for r in bruto:
        if not isinstance(r, dict):
            raise envios.ArquivoInvalido('Dados inválidos.')
        try:
            qtd = int(r.get('qtd') or 0)
            calc = int(r.get('qtd_calc') or 0)
        except (TypeError, ValueError):
            raise envios.ArquivoInvalido('Quantidade inválida.')
        if not 0 <= qtd <= 100000:
            raise envios.ArquivoInvalido('Quantidade fora do limite (0 a 100000).')
        mlbs = r.get('mlbs') if isinstance(r.get('mlbs'), list) else []
        saida.append(dict(
            incluir=r.get('incluir') is True and qtd > 0, qtd=qtd, qtd_calc=calc, apto=r.get('apto') if isinstance(r.get('apto'), bool) else None,
            status=r.get('status') if r.get('status') in envios.ROTULOS else envios.ST_SEM_DADOS,
            tipo=str(r.get('tipo') or '')[:20], codigo_ml=str(r.get('codigo_ml') or '')[:40], mlbu=str(r.get('mlbu') or '')[:40],
            sku=str(r.get('sku') or '')[:80], produto=str(r.get('produto') or '')[:200],
            mlbs=[str(m)[:30] for m in mlbs[:50]], motivo=str(r.get('motivo') or '')[:300],
            vendas=int(r.get('vendas') or 0), estoque_total=int(r.get('estoque_total') or 0),
            frete=r.get('frete'), custo=r.get('custo'), impacto_pct=r.get('impacto_pct'),
            urgencia=_sem_traco(r.get('urgencia'))[:30], prazo=_sem_traco(r.get('prazo'))[:20]))
    return saida


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _mlb_envio(r):
    """MLB que vai na coluna A (para copiar e colar no painel do ML): o primeiro anúncio do produto."""
    return str(r['mlbs'][0]) if r['mlbs'] else r['codigo_ml']


def _destacar_ab(ws, h):
    """Colunas A e B (MLB/SKU e quantidade) com cor própria; B em negrito. h = linha do cabeçalho."""
    from openpyxl.styles import Font, PatternFill
    for i in (1, 2):
        c = ws.cell(h, i)
        c.fill, c.font = PatternFill('solid', fgColor='B45F06'), Font(name='Arial', bold=True, color='FFFFFF')
    cor = PatternFill('solid', fgColor='FFF2CC')
    for n in range(h + 1, ws.max_row + 1):
        a, b = ws.cell(n, 1), ws.cell(n, 2)
        a.fill = b.fill = cor
        a.font, b.font = Font(name='Arial', size=10, bold=a.value == 'TOTAL'), Font(name='Arial', size=11, bold=True)
        a.number_format = '@'


def _apto_txt(r):
    return 'Sim' if r['apto'] is True else 'Não' if r['apto'] is False else '—'


def _gerar_xlsx_grupo(linhas, grupo, coleta=None):
    """Relatório separado: só os produtos do grupo (aptos ou não aptos para Full) que estão marcados para enviar,
    mais a aba 'Resumo por SKU' desses produtos."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    quer = grupo == 'aptos'
    sel = sorted([r for r in linhas if r['apto'] is quer and r['incluir'] and r['qtd'] > 0], key=lambda x: (x['sku'], x['codigo_ml']))
    marcados = sel
    agora = datetime.now(FUSO)
    coleta = coleta or {}
    wb = Workbook(); ws = wb.active; ws.title = ('Aptos para Full' if quer else 'Não aptos para Full')
    ws.append(['MLB', 'Quantidade para envio', 'Situação', 'Código ML', 'SKU', 'Produto', 'MLBs (anúncios)', 'Vendas 30 dias', 'Estoque no Full', 'Frete (R$)', 'Custo (R$)',
               'Frete / custo (%)', 'Qtd calculada', 'Tipo', 'Motivo'])
    for c in ws[1]:
        c.font, c.fill, c.alignment = Font(name='Arial', bold=True, color='FFFFFF'), PatternFill('solid', fgColor='1F3A5F'), Alignment(vertical='center', wrap_text=True)
    for r in sel:
        ws.append([_seguro(_mlb_envio(r)), r['qtd'], envios.ROTULOS[r['status']], _seguro(r['codigo_ml']), _seguro(r['sku']), _seguro(r['produto']),
                   _seguro(', '.join(r['mlbs'])), r['vendas'], r['estoque_total'], _num(r['frete']), _num(r['custo']), _num(r['impacto_pct']), r['qtd_calc'], r['tipo'], _seguro(r['motivo'])])
    ws.append(['TOTAL', sum(r['qtd'] for r in marcados)])
    for c in ws[ws.max_row]:
        c.font = Font(name='Arial', bold=True)
    for linha in ws.iter_rows(min_row=2, max_row=ws.max_row - 1):
        for c in linha:
            c.font = Font(name='Arial', size=10)
        for i in (9, 10):
            linha[i].number_format = '#,##0.00'
        linha[11].number_format = '0.0'
    for i, w in enumerate([18, 14, 18, 14, 22, 52, 34, 12, 12, 12, 12, 14, 12, 12, 60], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'; ws.auto_filter.ref = 'A1:%s%d' % (get_column_letter(15), ws.max_row - 1)
    _destacar_ab(ws, 1)
    # aba de resumo por SKU (só o que está marcado neste grupo)
    resumo = envios.resumo_sku(marcados)
    w2 = wb.create_sheet('Resumo por SKU')
    w2.append(['Data da coleta', coleta.get('data') or agora.strftime('%d/%m/%Y')]); w2.append(['Número da coleta', _seguro(coleta.get('numero') or '')]); w2.append([])
    for linha in w2.iter_rows(min_row=1, max_row=2):
        linha[0].font, linha[1].font = Font(name='Arial', bold=True), Font(name='Arial', size=11)
    w2.append(['SKU', 'Quantidade total'])
    for c in w2[4]:
        c.font, c.fill, c.alignment = Font(name='Arial', bold=True, color='FFFFFF'), PatternFill('solid', fgColor='1F3A5F'), Alignment(vertical='center')
    for t in resumo:
        w2.append([_seguro(t['sku']), t['qtd']])
    w2.append(['TOTAL', sum(t['qtd'] for t in resumo)])
    w2.column_dimensions['A'].width = 30; w2.column_dimensions['B'].width = 18
    w2.freeze_panes = 'A5'; w2.auto_filter.ref = 'A4:B%d' % w2.max_row
    _destacar_ab(w2, 4)
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    return buf, '%s_%s.xlsx' % ('aptos_full' if quer else 'nao_aptos_full', agora.strftime('%Y-%m-%d_%H%M')), len(sel)


def _gerar_xlsx(linhas, params, tipo, arquivo_ml='', coleta=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    enviar = [r for r in linhas if r['incluir']]
    resumo = envios.resumo_sku(enviar)
    agora = datetime.now(FUSO)
    wb = Workbook()
    cab_fonte = Font(name='Arial', bold=True, color='FFFFFF')
    cab_fill = PatternFill('solid', fgColor='1F3A5F')
    fonte = Font(name='Arial', size=10)

    def aba(ws, cabecalho, linhas_dados, larguras, total=None, fmt=None, topo=None, destacar=True):
        for t in (topo or []):
            ws.append(t)
        if topo:
            for linha in ws.iter_rows():
                linha[0].font = Font(name='Arial', bold=True)
                linha[1].font = Font(name='Arial', size=11)
            ws.append([])
        h = len(topo) + 2 if topo else 1        # linha do cabeçalho (depois do topo e de uma linha em branco)
        ws.append(cabecalho)
        for c in ws[h]:
            c.font, c.fill, c.alignment = cab_fonte, cab_fill, Alignment(vertical='center', wrap_text=True)
        for linha in linhas_dados:
            ws.append(linha)
        if total:
            ws.append(total)
            for c in ws[ws.max_row]:
                c.font = Font(name='Arial', bold=True)
        for linha in ws.iter_rows(min_row=h + 1):
            for c in linha:
                if c.font is None or not c.font.bold:
                    c.font = fonte
        for i, w in enumerate(larguras, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for col, f in (fmt or {}).items():
            for c in ws[col][h:]:
                c.number_format = f
        ws.freeze_panes = 'A%d' % (h + 1)
        ws.auto_filter.ref = '%s%d:%s%d' % ('A', h, get_column_letter(len(cabecalho)), ws.max_row)
        if destacar:
            _destacar_ab(ws, h)

    def origem(r):
        if r['status'] != envios.ST_ENVIAR:
            return 'Incluído manualmente (%s)' % envios.ROTULOS[r['status']].lower()
        if r['qtd'] != r['qtd_calc']:
            return 'Quantidade editada (calculado: %d)' % r['qtd_calc']
        return 'Regra'

    ws = wb.active
    if tipo == 'envio':
        ws.title = 'Enviar'
        aba(ws, ['MLB', 'Quantidade para envio', 'Código ML', 'Número do produto', 'SKU', 'Produto', 'MLBs (anúncios)', 'Tipo', 'Origem', 'Apto para Full', 'Urgência ML', 'Prazo limite'],
            [[_seguro(_mlb_envio(r)), r['qtd'], _seguro(r['codigo_ml']), _seguro(r['mlbu']), _seguro(r['sku']), _seguro(r['produto']), _seguro(', '.join(r['mlbs'])),
              r['tipo'] or 'Reposição', origem(r), _apto_txt(r), _seguro(r['urgencia']), _seguro(r['prazo'])] for r in sorted(enviar, key=lambda x: (x['sku'], x['codigo_ml']))],
            [18, 14, 14, 18, 22, 52, 34, 12, 30, 14, 14, 12], total=['TOTAL', sum(r['qtd'] for r in enviar)])
    ws2 = wb.active if tipo == 'producao' else wb.create_sheet()
    ws2.title = 'Resumo por SKU'
    coleta = coleta or {}
    aba(ws2, ['SKU', 'Quantidade total'], [[_seguro(t['sku']), t['qtd']] for t in resumo],
        [30, 18], total=['TOTAL', sum(t['qtd'] for t in resumo)],
        topo=[['Data da coleta', coleta.get('data') or agora.strftime('%d/%m/%Y')], ['Número da coleta', _seguro(coleta.get('numero') or '')]])
    if tipo == 'envio':
        fora = [r for r in linhas if not r['incluir']]
        ws3 = wb.create_sheet('Fora do envio')
        aba(ws3, ['MLB', 'Qtd calculada', 'Situação', 'Código ML', 'SKU', 'Produto', 'Vendas 30 dias', 'Estoque no Full', 'Frete (R$)', 'Custo (R$)', 'Frete / custo (%)', 'Motivo'],
            [[_seguro(_mlb_envio(r)), r['qtd_calc'], envios.ROTULOS[r['status']], _seguro(r['codigo_ml']), _seguro(r['sku']), _seguro(r['produto']), r['vendas'], r['estoque_total'],
              _num(r['frete']), _num(r['custo']), _num(r['impacto_pct']), _seguro(r['motivo'])]
             for r in sorted(fora, key=lambda x: (x['status'], x['sku']))],
            [18, 14, 18, 14, 22, 52, 14, 14, 12, 12, 16, 60], fmt={'I': '#,##0.00', 'J': '#,##0.00', 'K': '0.0'})
        wsp = wb.create_sheet('Parâmetros')
        for linha in (['Gerado em', agora.strftime('%d/%m/%Y %H:%M')], ['Relatório do ML', _seguro(arquivo_ml)],
                      ['Meses de estoque no Full', params.get('meses')], ['Limite de frete sobre o custo (%)', params.get('limite_frete_pct')],
                      ['Data da coleta', coleta.get('data') or agora.strftime('%d/%m/%Y')], ['Número da coleta', _seguro(coleta.get('numero') or '')],
                      ['Unidades do envio-teste', params.get('qtd_teste')], ['Produtos a enviar', len(enviar)],
                      ['Unidades a enviar', sum(r['qtd'] for r in enviar)], ['SKUs diferentes', len(resumo)],
                      ['Aptos para Full a enviar', sum(1 for r in enviar if r['apto'] is True)], ['Não aptos incluídos manualmente', sum(1 for r in enviar if r['apto'] is False)]):
            wsp.append(linha)
        wsp.column_dimensions['A'].width = 34
        wsp.column_dimensions['B'].width = 40
        for linha in wsp.iter_rows():
            linha[0].font = Font(name='Arial', bold=True)
            linha[1].font = fonte
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    sufixo = ''.join(ch for ch in str(coleta.get('numero') or '') if ch.isalnum() or ch in '-_')[:20]
    nome = '%s%s_%s.xlsx' % ('envio_full' if tipo == 'envio' else 'resumo_por_sku', ('_coleta' + sufixo) if sufixo else '', agora.strftime('%Y-%m-%d_%H%M'))
    return buf, nome


def _data(v):
    try:
        d = datetime.strptime(str(v), '%Y-%m-%d')
    except ValueError:
        raise envios.ArquivoInvalido('Data inválida.')
    return d


@app.post('/api/candidatos/pedidos')
def api_cand_pedidos():
    try:
        c = request.get_json(silent=True) or {}
        if c.get('conta') not in fontes.CONTAS:
            raise envios.ArquivoInvalido('Escolha a conta do Mercado Livre.')
        de, ate = _data(c.get('de')), _data(c.get('ate'))
        if ate < de or (ate - de).days > 31:
            raise envios.ArquivoInvalido('Período inválido (máximo de 31 dias por consulta).')
        off = c.get('offset', 0)
        if not isinstance(off, int) or isinstance(off, bool) or not 0 <= off <= 20000:
            raise envios.ArquivoInvalido('Posição inválida.')
        r = fontes.pedidos_pagina(c['conta'], de.strftime('%Y-%m-%d'), ate.strftime('%Y-%m-%d'), off)
        return jsonify(ok=True, **r)
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/candidatos/itens')
def api_cand_itens():
    try:
        c = request.get_json(silent=True) or {}
        ids = c.get('ids')
        if c.get('conta') not in fontes.CONTAS:
            raise envios.ArquivoInvalido('Escolha a conta do Mercado Livre.')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or not all(isinstance(i, str) and fontes._MLB.match(i) for i in ids):
            raise envios.ArquivoInvalido('Lista de anúncios inválida.')
        return jsonify(ok=True, itens=fontes.itens_info(c['conta'], ids))
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/candidatos/exportar')
def api_cand_exportar():
    try:
        c = request.get_json(silent=True) or {}
        bruto = c.get('linhas')
        if not isinstance(bruto, list) or not 1 <= len(bruto) <= 20000:
            raise envios.ArquivoInvalido('Nada para exportar.')
        linhas = []
        for r in bruto:
            if not isinstance(r, dict):
                raise envios.ArquivoInvalido('Dados inválidos.')
            linhas.append({k: (_seguro(str(r.get(k) or ''))[:200] if k in ('conta', 'sku', 'produto', 'mlbs', 'envio', 'situacao') else _num(r.get(k)))
                           for k in ('conta', 'sku', 'produto', 'mlbs', 'envio', 'situacao', 'unidades', 'pedidos', 'valor', 'preco_medio', 'por_dia', 'custo', 'custo_total', 'estoque')})
        buf, nome = _gerar_xlsx_candidatos(linhas, str(c.get('periodo') or '')[:60])
        return send_file(buf, mimetype=XLSX, as_attachment=True, download_name=nome)
    except Exception as exc:
        return _erro(exc)


def _gerar_xlsx_candidatos(linhas, periodo):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook(); ws = wb.active; ws.title = 'Candidatos ao Full'
    cab = ['Conta', 'SKU', 'Produto', 'MLBs', 'Envio atual', 'Situação', 'Unidades', 'Pedidos', 'Faturamento (R$)', 'Preço médio (R$)', 'Vendas/dia', 'Custo unit. (R$)', 'Custo total (R$)', 'Estoque do anúncio']
    ws.append(cab)
    for r in linhas:
        ws.append([r['conta'], r['sku'], r['produto'], r['mlbs'], r['envio'], r['situacao'], r['unidades'], r['pedidos'], r['valor'], r['preco_medio'], r['por_dia'], r['custo'], r['custo_total'], r['estoque']])
    for c in ws[1]:
        c.font = Font(name='Arial', bold=True, color='FFFFFF'); c.fill = PatternFill('solid', fgColor='1F3A5F'); c.alignment = Alignment(wrap_text=True, vertical='center')
    for linha in ws.iter_rows(min_row=2):
        for c in linha:
            c.font = Font(name='Arial', size=10)
        for i in (8, 9, 10, 12, 13):
            linha[i].number_format = '#,##0.00'
    for i, w in enumerate([8, 22, 50, 28, 18, 12, 10, 10, 16, 16, 11, 14, 15, 12], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
    ws2 = wb.create_sheet('Parâmetros')
    ws2.append(['Gerado em', datetime.now(FUSO).strftime('%d/%m/%Y %H:%M')]); ws2.append(['Período', periodo])
    ws2.append(['Observação', 'Vendas de anúncios que não são Full hoje. Envio atual = tipo de envio do anúncio agora; custo da planilha de custos.'])
    ws2.column_dimensions['A'].width = 14; ws2.column_dimensions['B'].width = 90
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    return buf, 'candidatos_full_%s.xlsx' % datetime.now(FUSO).strftime('%Y-%m-%d_%H%M')


@app.post('/api/envios/exportar')
def api_exportar():
    try:
        corpo = request.get_json(silent=True) or {}
        tipo = corpo.get('tipo')
        if tipo not in ('envio', 'producao', 'aptos', 'nao_aptos'):
            raise envios.ArquivoInvalido('Tipo de planilha inválido.')
        linhas = _linhas_validas(corpo.get('linhas'))
        if tipo in ('envio', 'producao') and not any(r['incluir'] for r in linhas):
            raise envios.ArquivoInvalido('Nenhum produto marcado para enviar.')
        params = corpo.get('parametros') if isinstance(corpo.get('parametros'), dict) else {}
        col = corpo.get('coleta') if isinstance(corpo.get('coleta'), dict) else {}
        try:
            data = datetime.strptime(str(col.get('data') or ''), '%Y-%m-%d').strftime('%d/%m/%Y')
        except ValueError:
            data = ''
        coleta = {'data': data, 'numero': str(col.get('numero') or '').strip()[:30]}
        if tipo in ('aptos', 'nao_aptos'):
            buf, nome, n = _gerar_xlsx_grupo(linhas, tipo, coleta)
            if not n:
                raise envios.ArquivoInvalido('Nenhum produto %s está marcado para enviar.' % ('apto' if tipo == 'aptos' else 'não apto'))
            return send_file(buf, mimetype=XLSX, as_attachment=True, download_name=nome)
        buf, nome = _gerar_xlsx(linhas, params, tipo, str(corpo.get('arquivo') or '')[:120], coleta)
        return send_file(buf, mimetype=XLSX, as_attachment=True, download_name=nome)
    except Exception as exc:
        return _erro(exc)


@app.errorhandler(413)
def _grande(_):
    return jsonify(ok=False, error='Arquivo grande demais (limite de 12 MB).'), 413


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)


# ------------------------------------------------------------------ Vendas do dia (lucro por venda)
_CAMPOS_TXT = ('conta', 'pedido', 'data', 'mlb', 'sku', 'titulo', 'grupo', 'logistica', 'status_envio', 'substatus', 'status_rotulo', 'obs', 'meta_origem')


def _params_vendas(c):
    try:
        return vendas.validar_parametros((c or {}).get('parametros'))
    except vendas.ParametroInvalido as exc:
        raise envios.ArquivoInvalido(str(exc))


@app.post('/api/vendas/pedidos')
def api_vendas_pedidos():
    try:
        c = request.get_json(silent=True) or {}
        if c.get('conta') not in fontes.CONTAS:
            raise envios.ArquivoInvalido('Escolha a conta do Mercado Livre.')
        de, ate = _data(c.get('de')), _data(c.get('ate'))
        if ate < de or (ate - de).days > 31:
            raise envios.ArquivoInvalido('Período inválido (máximo de 31 dias por consulta).')
        off = c.get('offset', 0)
        if not isinstance(off, int) or isinstance(off, bool) or not 0 <= off <= 20000:
            raise envios.ArquivoInvalido('Posição inválida.')
        r = fontes.pedidos_financeiros(c['conta'], de.strftime('%Y-%m-%d'), ate.strftime('%Y-%m-%d'), off)
        return jsonify(ok=True, **r)
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/vendas/impostos')
def api_vendas_impostos():
    """Imposto/embalagem (%) da aba TaxasML por conta. Falha de uma conta não derruba as outras."""
    try:
        c = request.get_json(silent=True) or {}
        contas = c.get('contas')
        if not isinstance(contas, list) or not 1 <= len(contas) <= 2 or not all(x in fontes.CONTAS for x in contas):
            raise envios.ArquivoInvalido('Contas inválidas.')
        ok, erros = {}, {}
        for conta in contas:
            try:
                ok[conta] = fontes.impostos_conta(conta)
            except fontes.FonteIndisponivel as exc:
                erros[conta] = str(exc)
        return jsonify(ok=True, impostos=ok, erros=erros)
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/vendas/cadastro')
def api_vendas_cadastro():
    try:
        c = request.get_json(silent=True) or {}
        skus = c.get('skus')
        if not isinstance(skus, list) or not 1 <= len(skus) <= 100 or not all(isinstance(x, str) and len(x) <= 80 for x in skus):
            raise envios.ArquivoInvalido('Lista de SKUs inválida.')
        return jsonify(ok=True, cadastro=fontes.cadastro(skus))
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/vendas/calcular')
def api_vendas_calcular():
    try:
        c = request.get_json(silent=True) or {}
        params = _params_vendas(c)
        bruto, cad = c.get('linhas'), c.get('cadastro')
        if not isinstance(bruto, list) or len(bruto) > 20000 or not isinstance(cad, dict):
            raise envios.ArquivoInvalido('Dados inválidos.')
        linhas = [{k: (str(r.get(k) or '')[:200] if k in _CAMPOS_TXT else _num(r.get(k))) for k in _CAMPOS_TXT + ('quantidade', 'venda', 'tarifa', 'frete')} for r in bruto if isinstance(r, dict)]
        cadastro = {str(k): {'custo': _num(v.get('custo')), 'margem': _num(v.get('margem')), 'margem_full': _num(v.get('margem_full'))} for k, v in cad.items() if isinstance(v, dict)}
        analisadas, resumo = vendas.calcular(linhas, cadastro, params)
        return jsonify(ok=True, linhas=analisadas, resumo=resumo)
    except Exception as exc:
        return _erro(exc)


def _linhas_vendas(c):
    """Linhas vindas da tela, recalculadas aqui (o lucro nunca é aceito pronto) e com a marca 'não enviar'."""
    params = _params_vendas(c)
    bruto = c.get('linhas')
    if not isinstance(bruto, list) or not 1 <= len(bruto) <= 20000:
        raise envios.ArquivoInvalido('Nada para exportar.')
    saida = []
    for r in bruto:
        if not isinstance(r, dict):
            raise envios.ArquivoInvalido('Dados inválidos.')
        base = {k: (str(r.get(k) or '')[:200] if k in _CAMPOS_TXT else _num(r.get(k))) for k in _CAMPOS_TXT + ('quantidade', 'venda', 'tarifa', 'frete')}
        meta = _num(r.get('meta'))
        cad = {'custo': _num(r.get('custo_unit')), 'margem': meta, 'margem_full': meta}
        a = vendas.analisar(base, cad, params)
        a['meta_origem'] = base['meta_origem'] or a['meta_origem']
        a['nao_enviar'] = r.get('nao_enviar') is True
        saida.append(a)
    return saida, params


def _periodo(c):
    return str(c.get('periodo') or '')[:60]


def _motivo_nao_enviar(l):
    if l['classe'] == 'prejuizo':
        return 'Prejuízo de R$ %.2f (margem %.2f%%)' % (-l['lucro'], l['margem'])
    if l['classe'] == 'abaixo':
        return 'Margem %.2f%% abaixo do mínimo de %.2f%%' % (l['margem'], l['meta'])
    if l['classe'] == 'dentro':
        return 'Marcado pelo operador (margem %.2f%%)' % l['margem']
    return 'Marcado pelo operador (%s)' % (l['pendencias'] or 'sem dados')


CAB_DIA = ['Não enviar', 'Situação', 'Prejuízo / deixou de ganhar (R$)', 'Conta', 'Pedido', 'Data', 'SKU', 'Status do envio', 'MLB', 'Produto', 'Qtd', 'Venda (R$)', 'Tarifa ML (R$)',
           'Frete vendedor (R$)', 'Repasse (R$)', 'Imposto (R$)', 'Custo total (R$)', 'Lucro (R$)', 'Margem real (%)', 'Margem mínima (%)', 'Tipo de envio', 'Observação']
ENVIO_ROTULO = {'a_enviar': 'A enviar', 'enviado': 'Já enviado', 'full': 'Full'}


def _linha_dia(l):
    return [_seguro('SIM' if l['nao_enviar'] else ''), vendas.ROTULOS[l['classe']], l['perda'] or None, l['conta'], l['pedido'], l['data'], _seguro(l['sku']), _seguro(l['status_rotulo']), l['mlb'],
            _seguro(l['titulo']), l['quantidade'], l['venda'], l['tarifa'], l['frete'], l['repasse'], l['imposto'], l['custo_total'], l['lucro'], l['margem'], l['meta'],
            ENVIO_ROTULO.get(l['grupo'], l['grupo']), _seguro(l['pendencias'] or l['obs'])]


CAB_NAO = ['Pedido', 'Conta', 'Data', 'SKU', 'Status do envio', 'MLB', 'Produto', 'Qtd', 'Venda (R$)', 'Lucro (R$)', 'Margem real (%)', 'Margem mínima (%)', 'Prejuízo / deixou de ganhar (R$)', 'Motivo']


def _linha_nao(l):
    return [l['pedido'], l['conta'], l['data'], _seguro(l['sku']), _seguro(l['status_rotulo']), l['mlb'], _seguro(l['titulo']), l['quantidade'], l['venda'], l['lucro'], l['margem'], l['meta'],
            l['perda'] or None, _motivo_nao_enviar(l)]


def _resumo_linhas(linhas, params, periodo):
    r = vendas.resumo(linhas)
    return [['Período', periodo], ['Gerado em', datetime.now(FUSO).strftime('%d/%m/%Y %H:%M')], ['Vendas analisadas (itens)', r['linhas']], ['Unidades', r['unidades']],
            ['Dentro da margem', r['dentro']], ['Abaixo da margem', r['abaixo']], ['Prejuízo', r['prejuizo']], ['Sem dados', r['sem_dados']],
            ['Vendido, só com dados (R$)', r['venda']], ['Lucro, só com dados (R$)', r['lucro']], ['Prejuízo total (R$)', r['prejuizo_rs']], ['Deixou de ganhar nas abaixo da margem (R$)', r['deixou_rs']], ['Marcados para não enviar', sum(1 for l in linhas if l['nao_enviar'])],
            ['Imposto (% da venda)', ('%s (digitado na tela)' % float(params['imposto_pct'])) if params['imposto_pct'] is not None else 'da planilha (aba TaxasML, por conta e Full/normal)'], ['Margem mínima padrão (%)', float(params['margem_padrao'])],
            ['Regra', 'Lucro = venda − tarifa ML − frete − imposto − custo. Margem = lucro ÷ custo. Dado ausente não entra na conta ("Sem dados").']]


@app.post('/api/vendas/exportar')
def api_vendas_exportar():
    try:
        c = request.get_json(silent=True) or {}
        linhas, params = _linhas_vendas(c)
        tipo = c.get('tipo')
        periodo = _periodo(c)
        if tipo == 'nao_enviar':
            marcadas = [l for l in linhas if l['nao_enviar']]
            if not marcadas:
                raise envios.ArquivoInvalido('Marque pelo menos uma venda para não enviar.')
            buf = _gerar_xlsx_vendas('Não enviar', CAB_NAO, [_linha_nao(l) for l in marcadas], [8, 8, 17, 22, 20, 15, 50, 6, 12, 12, 12, 12, 16, 60], (8, 9, 10, 11, 12, 13), _resumo_linhas(linhas, params, periodo))
            nome = 'nao_enviar_%s.xlsx' % datetime.now(FUSO).strftime('%Y-%m-%d_%H%M')
        elif tipo == 'prejuizo':
            neg = sorted([l for l in linhas if l['classe'] == 'prejuizo'], key=lambda l: -l['perda'])
            if not neg:
                raise envios.ArquivoInvalido('Nenhuma venda com prejuízo nesta lista.')
            buf = _gerar_xlsx_vendas('Prejuízo', CAB_DIA, [_linha_dia(l) for l in neg], [10, 17, 16, 7, 17, 16, 20, 20, 15, 46, 5, 11, 11, 11, 11, 11, 12, 11, 10, 10, 11, 50], (3, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20), _resumo_linhas(linhas, params, periodo))
            nome = 'vendas_com_prejuizo_%s.xlsx' % datetime.now(FUSO).strftime('%Y-%m-%d_%H%M')
        elif tipo == 'dia':
            buf = _gerar_xlsx_vendas('Vendas do dia', CAB_DIA, [_linha_dia(l) for l in linhas], [10, 17, 16, 7, 17, 16, 20, 20, 15, 46, 5, 11, 11, 11, 11, 11, 12, 11, 10, 10, 11, 50], (3, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20), _resumo_linhas(linhas, params, periodo))
            nome = 'vendas_do_dia_%s.xlsx' % datetime.now(FUSO).strftime('%Y-%m-%d_%H%M')
        else:
            raise envios.ArquivoInvalido('Tipo de relatório inválido.')
        return send_file(buf, mimetype=XLSX, as_attachment=True, download_name=nome)
    except Exception as exc:
        return _erro(exc)


def _gerar_xlsx_vendas(titulo, cab, linhas, larguras, colunas_num, resumo):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook(); ws = wb.active; ws.title = titulo
    ws.append(cab)
    for l in linhas:
        ws.append(l)
    for c in ws[1]:
        c.font = Font(name='Arial', bold=True, color='FFFFFF'); c.fill = PatternFill('solid', fgColor='1F3A5F'); c.alignment = Alignment(wrap_text=True, vertical='center')
    vermelho, ambar = PatternFill('solid', fgColor='FDEAEA'), PatternFill('solid', fgColor='FFF4D6')
    for linha in ws.iter_rows(min_row=2):
        for c in linha:
            c.font = Font(name='Arial', size=10)
        for i in colunas_num:
            if isinstance(linha[i - 1].value, float):
                linha[i - 1].number_format = '#,##0.00'
        sit = str(linha[1].value or '')
        if sit == 'Prejuízo':
            linha[1].fill = vermelho
        elif sit in ('Abaixo da margem', 'Sem dados'):
            linha[1].fill = ambar
        if linha[0].value == 'SIM':
            linha[0].font = Font(name='Arial', size=10, bold=True, color='B4232A')
    for i, w in enumerate(larguras, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
    ws2 = wb.create_sheet('Resumo')
    for linha in resumo:
        ws2.append([linha[0], _seguro(linha[1]) if isinstance(linha[1], str) else linha[1]])
    for linha in ws2.iter_rows():
        linha[0].font = Font(name='Arial', bold=True); linha[1].font = Font(name='Arial')
    ws2.column_dimensions['A'].width = 32; ws2.column_dimensions['B'].width = 90
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    return buf


@app.post('/api/vendas/salvar')
def api_vendas_salvar():
    try:
        c = request.get_json(silent=True) or {}
        linhas, params = _linhas_vendas(c)
        dia = _data(c.get('dia')).strftime('%Y-%m-%d')
        r = vendas.resumo(linhas)
        marcadas = [l for l in linhas if l['nao_enviar']]
        payload = {'dia': dia, 'periodo': _periodo(c), 'parametros': {'imposto_pct': None if params['imposto_pct'] is None else float(params['imposto_pct']), 'margem_padrao': float(params['margem_padrao'])},
                   'resumo': {'itens': r['linhas'], 'unidades': r['unidades'], 'dentro': r['dentro'], 'abaixo': r['abaixo'], 'prejuizo': r['prejuizo'], 'sem_dados': r['sem_dados'],
                              'venda': r['venda'], 'lucro': r['lucro'], 'prejuizo_rs': r['prejuizo_rs'], 'deixou_rs': r['deixou_rs'], 'nao_enviar': len(marcadas)},
                   'cab': CAB_DIA, 'linhas': [_linha_dia(l) for l in linhas],
                   'cab_nao_enviar': CAB_NAO, 'nao_enviar': [_linha_nao(l) for l in marcadas]}
        return jsonify(ok=True, **fontes.salvar_relatorio(payload))
    except Exception as exc:
        return _erro_fonte(exc)


# ------------------------------------------------------------------ Preço necessário e atualização de preço no ML
def _imposto_item(x, params):
    """Imposto (%) do item: o que a tela usou na conta dele; se não vier, o digitado. Sem nenhum dos dois, recusa."""
    v = _decimal_ou_none(x.get('imposto_pct'))
    if v is None:
        v = params.get('imposto_pct')
    if v is None or not 0 <= v < 60:
        raise envios.ArquivoInvalido('Imposto não informado (planilha TaxasML indisponível). Digite o imposto na tela.')
    return v


def _decimal_ou_none(v):
    try:
        d = precos.D(str(v))
    except Exception:
        return None
    return d if d.is_finite() else None


@app.post('/api/vendas/preco-alvo')
def api_vendas_preco_alvo():
    """Preço necessário para chegar na margem, por anúncio (somente leitura)."""
    try:
        c = request.get_json(silent=True) or {}
        if c.get('conta') not in fontes.CONTAS:
            raise envios.ArquivoInvalido('Escolha a conta do Mercado Livre.')
        itens = c.get('itens')
        if not isinstance(itens, list) or not 1 <= len(itens) <= 6:
            raise envios.ArquivoInvalido('Envie de 1 a 6 anúncios por vez.')
        params = _params_vendas(c)
        pedidos = []
        for x in itens:
            mlb = str((x or {}).get('mlb') or '').strip().upper() if isinstance(x, dict) else ''
            if not fontes._MLB.match(mlb):
                raise envios.ArquivoInvalido('Anúncio inválido.')
            pedidos.append((mlb, _decimal_ou_none(x.get('custo')), _decimal_ou_none(x.get('meta')), _imposto_item(x, params)))
        tk = fontes.token(c['conta'])
        vendedor = fontes._vendedor(c['conta'], tk)

        def um(p):
            mlb, custo, meta, imposto = p
            try:
                return mlb, precos.analisar(tk, vendedor, precos.buscar_item(tk, mlb), custo, meta, imposto)
            except precos.Bloqueio as exc:
                return mlb, {'mlb': mlb, 'acao': 'bloqueado', 'mensagem': str(exc)}
            except Exception as exc:
                log.exception('Falha ao calcular o preço de %s', mlb)
                return mlb, {'mlb': mlb, 'acao': 'bloqueado', 'mensagem': 'Não consegui calcular agora (%s)' % str(exc)[:60]}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            res = dict(ex.map(um, pedidos))
        return jsonify(ok=True, somente_leitura=True, resultados=res)
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/vendas/preco-aplicar')
def api_vendas_preco_aplicar():
    """Altera o preço dos anúncios marcados. Recalcula tudo na hora e nunca confia no que o navegador mostrou."""
    try:
        c = request.get_json(silent=True) or {}
        if c.get('conta') not in fontes.CONTAS:
            raise envios.ArquivoInvalido('Escolha a conta do Mercado Livre.')
        if c.get('confirmacao') != precos.CONFIRMACAO:
            raise envios.ArquivoInvalido('Confirmação obrigatória para alterar preço.')
        itens = c.get('itens')
        if not isinstance(itens, list) or not 1 <= len(itens) <= precos.MAX_APLICAR:
            raise envios.ArquivoInvalido('Envie de 1 a %d anúncios por vez.' % precos.MAX_APLICAR)
        params = _params_vendas(c)
        liberar = c.get('liberar_acima_limite') is True
        pedidos, vistos = [], set()
        for x in itens:
            if not isinstance(x, dict):
                raise envios.ArquivoInvalido('Item inválido.')
            mlb = str(x.get('mlb') or '').strip().upper()
            custo, meta = _decimal_ou_none(x.get('custo')), _decimal_ou_none(x.get('meta'))
            visto_atual, visto_novo = _decimal_ou_none(x.get('preco_atual_visto')), _decimal_ou_none(x.get('preco_novo_visto'))
            if not fontes._MLB.match(mlb) or mlb in vistos or None in (custo, meta, visto_atual, visto_novo) or visto_atual <= 0 or visto_novo <= 0:
                raise envios.ArquivoInvalido('Item inválido ou repetido.')
            vistos.add(mlb)
            pedidos.append((mlb, custo, meta, visto_atual, visto_novo, _imposto_item(x, params)))
        conta = c['conta']
        tk = fontes.token(conta)
        vendedor = fontes._vendedor(conta, tk)
        resultados, aplicados = [], []
        for mlb, custo, meta, visto_atual, visto_novo, imposto in pedidos:
            r = {'mlb': mlb, 'status': 'PULADO', 'mensagem': ''}
            try:
                item = precos.buscar_item(tk, mlb)
                a = precos.analisar(tk, vendedor, item, custo, meta, imposto)
                if a['acao'] != 'subir':
                    raise precos.Bloqueio('Não precisa mais de ajuste: ' + a['mensagem'])
                atual, novo = precos.D(str(a['preco_atual'])), precos.D(str(a['preco_novo']))
                if abs(atual - visto_atual) > precos.D('0.009'):
                    raise precos.Bloqueio('O preço mudou no Mercado Livre desde que você viu. Calcule de novo.')
                if abs(novo / visto_novo - 1) * 100 > precos.DIVERGENCIA_MAX_PCT:
                    raise precos.Bloqueio('Custo, tarifa ou frete mudaram desde que você viu. Calcule de novo.')
                if a['requer_liberacao'] and not liberar:
                    raise precos.Bloqueio('Aumento acima de %s%% exige a liberação extra.' % precos.LIMITE_VARIACAO_PCT)
                if not precos.reservar(conta, mlb):
                    raise precos.Bloqueio('Este anúncio acabou de ser alterado. Aguarde um minuto.')
                ok, lido = precos.enviar_preco(tk, item, novo)
                r.update(status='OK' if ok else 'PENDENTE', preco_anterior=a['preco_atual'], preco_novo=a['preco_novo'], verificado=lido,
                         mensagem='Preço alterado e confirmado no Mercado Livre.' if ok else 'Enviado, mas o Mercado Livre ainda não confirmou. Confira no anúncio.')
                log.info('PREÇO %s %s: %s -> %s (lido %s) %s', conta, mlb, atual, novo, lido, r['status'])
                if ok:
                    aplicados.append({'mlb': mlb, 'preco_referencia_anterior': float(atual), 'preco_atual_ml': float(novo)})
            except precos.Bloqueio as exc:
                r['mensagem'] = str(exc)
            except Exception as exc:
                log.exception('Falha ao alterar o preço de %s', mlb)
                r.update(status='ERRO', mensagem=str(exc)[:200])
            resultados.append(r)
        if aplicados:
            plan = fontes.atualizar_referencia(conta, aplicados)
            for r in resultados:
                if r['mlb'] in plan:
                    r['planilha'] = plan[r['mlb']]
        return jsonify(ok=True, conta=conta, somente_leitura=False, resultados=resultados, atualizados=sum(r['status'] == 'OK' for r in resultados))
    except Exception as exc:
        return _erro_fonte(exc)


@app.post('/api/vendas/exportar-manual')
def api_vendas_exportar_manual():
    """Lista do que não foi calculado ou atualizado, para a pessoa mexer na mão no Mercado Livre."""
    try:
        c = request.get_json(silent=True) or {}
        bruto = c.get('itens')
        if not isinstance(bruto, list) or not 1 <= len(bruto) <= 5000:
            raise envios.ArquivoInvalido('Nada para exportar.')
        linhas = []
        for r in bruto:
            if not isinstance(r, dict):
                raise envios.ArquivoInvalido('Dados inválidos.')
            t = lambda k: _seguro(str(r.get(k) or '')[:300])
            linhas.append([t('tipo'), t('conta'), t('mlb'), t('sku'), t('produto'), t('situacao'), _num(r.get('preco_atual')), _num(r.get('preco_necessario')), t('motivo')])
        buf = _gerar_xlsx_vendas('Fazer na mão', ['O que houve', 'Conta', 'MLB', 'SKU', 'Produto', 'Situação', 'Preço atual (R$)', 'Preço necessário (R$)', 'Motivo / o que fazer'], linhas,
                                 [20, 7, 15, 22, 50, 18, 14, 16, 80], (7, 8), [['Período', str(c.get('periodo') or '')[:60]], ['Gerado em', datetime.now(FUSO).strftime('%d/%m/%Y %H:%M')],
                                 ['Observação', 'Itens que o painel não calculou ou não conseguiu atualizar. Altere o preço direto no Mercado Livre.']])
        return send_file(buf, mimetype=XLSX, as_attachment=True, download_name='fazer_na_mao_%s.xlsx' % datetime.now(FUSO).strftime('%Y-%m-%d_%H%M'))
    except Exception as exc:
        return _erro(exc)
