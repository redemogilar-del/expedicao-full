"use strict";
// Aba Shopee: pedidos a enviar, lucro de cada venda e preço necessário. Sem innerHTML (CSP).
const $ = id => document.getElementById(id);
const el = (tag, props, ...filhos) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) { if (k === 'class') e.className = v; else e[k] = v; }
  for (const f of filhos) if (f !== null && f !== undefined) e.append(f instanceof Node ? f : String(f));
  return e;
};
const num = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR');
const brl = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'});
const pct = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + '%';
const ROT = {prejuizo: 'Prejuízo', abaixo: 'Abaixo da margem', dentro: 'Dentro da margem', sem_dados: 'Sem dados'};
const PAINEL = $('sPainelPreco');
const CONFIRMACAO = PAINEL.dataset.confirmacao, MAX_APLICAR = Number(PAINEL.dataset.max) || 5, LIMITE = Number(PAINEL.dataset.limite) || 15;

let RAW = [], CAD = null, LINHAS = [], SEL = new Set(), IMPOSTOS = {}, PRECO = {}, IMPOSTO_USADO = null, busca = '';
const CLASSES = new Set();
const chaveAnuncio = r => r.mlb + '|' + r.modelo;

function mensagem(texto, tipo) { const m = $('sMsg'); m.textContent = texto || ''; m.hidden = !texto; m.className = 'aviso' + (tipo ? ' ' + tipo : ''); }
function progresso(texto, p) {
  $('sProg').hidden = texto === null; if (texto === null) return;
  $('sBarra').style.width = Math.max(2, Math.min(100, p || 0)) + '%'; $('sTxt').textContent = texto;
}
async function json(url, corpo) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corpo)});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sessão expirada'); }
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok || !d || d.ok === false) throw new Error((d && d.error) || ('Erro ' + r.status + '. O servidor pode estar acordando; tente de novo em 1 minuto.'));
  return d;
}
const parametros = () => ({imposto_pct: $('sImposto').value, margem_padrao: $('sMargem').value, impostos: IMPOSTOS});

// ---------- busca e cálculo ----------
async function buscar() {
  $('sBuscar').disabled = true; mensagem(''); $('sResultado').hidden = true;
  try {
    RAW = []; CAD = null; SEL.clear(); PRECO = {};
    progresso('Lendo os pedidos da Shopee…', 15);
    const d = await json('/api/shopee/pedidos', {dias: Number($('sDias').value)});
    RAW = d.linhas;
    progresso('Lendo o imposto da planilha…', 55);
    try { IMPOSTOS = (await json('/api/vendas/impostos', {contas: ['ML19', 'ML44']})).impostos; } catch (e) { IMPOSTOS = {}; }
    progresso('Buscando custo e margem e calculando…', 75);
    await calcular();
    progresso(null);
    $('sResultado').hidden = false;
    mensagem(d.pedidos + ' pedido(s) a enviar.' + (d.sem_renda ? ' ' + d.sem_renda + ' ainda sem o valor da Shopee (aparecem como "Sem dados").' : ''), 'ok');
  } catch (e) {
    progresso(null); mensagem(e.message, 'erro');
  } finally { $('sBuscar').disabled = false; }
}

async function calcular() {
  if (!RAW.length) { LINHAS = []; desenhar(); return; }
  const d = await json('/api/shopee/calcular', {linhas: RAW, parametros: parametros(), cadastro: CAD});
  if (!CAD) CAD = d.cadastro;
  IMPOSTO_USADO = d.imposto_pct;
  LINHAS = d.linhas.map((l, i) => Object.assign(l, {_i: i}));
  desenhar();
}

// ---------- tela ----------
function visiveis() {
  const b = busca.trim().toLowerCase();
  return LINHAS.filter(r => (!CLASSES.size || CLASSES.has(r.classe)) &&
    (!b || [r.pedido, r.sku, r.titulo, r.variacao, r.mlb].some(x => String(x || '').toLowerCase().includes(b))));
}

function desenhar() {
  const c = {prejuizo: 0, abaixo: 0, dentro: 0, sem_dados: 0}; let prej = 0, deixou = 0;
  LINHAS.forEach(r => { c[r.classe]++; if (r.perda_tipo === 'prejuizo') prej += r.perda; else if (r.perda_tipo === 'deixou') deixou += r.perda; });
  $('sCartoes').replaceChildren(
    el('div', {class: 'card'}, el('b', {}, num(LINHAS.length)), el('span', {}, 'itens vendidos')),
    el('div', {class: 'card destaque'}, el('b', {}, num(c.dentro)), el('span', {}, 'dentro da margem')),
    el('div', {class: 'card'}, el('b', {}, num(c.abaixo)), el('span', {}, 'abaixo da margem · deixou de ganhar ' + brl(deixou))),
    el('div', {class: 'card risco'}, el('b', {}, num(c.prejuizo)), el('span', {}, 'com prejuízo · perdeu ' + brl(prej))),
    el('div', {class: 'card'}, el('b', {}, num(c.sem_dados)), el('span', {}, 'sem dados')),
    el('div', {class: 'card'}, el('b', {}, IMPOSTO_USADO === null ? '—' : pct(IMPOSTO_USADO)), el('span', {}, 'imposto usado')));
  const cb = $('sClasses'); cb.replaceChildren();
  cb.append(el('button', {class: !CLASSES.size ? 'on' : '', onclick: () => { CLASSES.clear(); desenhar(); }}, 'Todas (' + LINHAS.length + ')'));
  for (const k of ['prejuizo', 'abaixo', 'dentro', 'sem_dados'])
    cb.append(el('button', {class: CLASSES.has(k) ? 'on' : '', title: 'Clique em mais de um para juntar os filtros', onclick: () => { CLASSES.has(k) ? CLASSES.delete(k) : CLASSES.add(k); desenhar(); }}, ROT[k] + ' (' + c[k] + ')'));
  const lista = visiveis();
  $('sCorpo').replaceChildren(...lista.map(linha));
  $('sVazio').hidden = lista.length > 0;
  const marcadas = LINHAS.filter(r => SEL.has(r._i));
  $('sCalcular').disabled = !marcadas.some(r => r.custo_unit && r.meta !== null);
  $('sPreco').disabled = !marcadas.some(r => (PRECO[chaveAnuncio(r)] || {}).acao === 'subir');
}

function taxas(r) {
  const partes = [['comissão', r.comissao], ['taxa de serviço', r.servico], ['cupom da loja', r.cupom_loja], ['frete', r.frete_vendedor]].filter(([, v]) => v);
  return el('td', {class: 'n', title: partes.length ? partes.map(([k, v]) => k + ' ' + brl(v)).join(' · ') : ''}, brl(r.tarifa));
}

function celulaPreco(r) {
  const p = PRECO[chaveAnuncio(r)];
  if (!p) return el('td', {class: 'n mlbs'}, '—');
  if (p.acao === 'bloqueado') return el('td', {class: 'mlbs', title: p.mensagem}, 'não calculado: ' + p.mensagem);
  if (p.acao === 'ok') return el('td', {class: 'n', title: p.mensagem}, brl(p.preco_atual), el('div', {class: 'mlbs'}, 'já atinge'));
  return el('td', {class: 'n', title: p.mensagem}, brl(p.preco_novo), el('div', {class: 'mlbs'}, 'hoje ' + brl(p.preco_atual) + ' (+' + pct(p.variacao_pct) + ')'));
}

function linha(r) {
  const cx = el('input', {type: 'checkbox', checked: SEL.has(r._i), onchange: e => { e.target.checked ? SEL.add(r._i) : SEL.delete(r._i); desenhar(); }});
  const tr = el('tr', {class: SEL.has(r._i) ? 'marcado' : ''},
    el('td', {class: 'chk'}, cx),
    el('td', {}, el('span', {class: 'tag v-' + r.classe, title: r.pendencias ? 'Falta: ' + r.pendencias : (r.obs || '')}, ROT[r.classe])),
    el('td', {class: 's-pedido'}, el('span', {class: 'pedido-n'}, r.pedido), el('div', {class: 'mlbs'}, 'pedido ' + r.data),
      el('div', {class: 'mlbs'}, (r.status_rotulo || '—') + (r.limite_dt ? ' · enviar até ' + r.limite_dt : ''))),
    el('td', {}, r.sku || '(sem SKU)'),
    el('td', {}, r.titulo, el('div', {class: 'mlbs'}, (r.variacao ? r.variacao + ' · ' : '') + 'anúncio ' + r.mlb)),
    el('td', {class: 'n'}, num(r.quantidade)),
    el('td', {class: 'n'}, brl(r.venda)),
    taxas(r),
    el('td', {class: 'n'}, brl(r.liquido)),
    el('td', {class: 'n'}, brl(r.imposto)),
    el('td', {class: 'n'}, brl(r.custo_total)),
    el('td', {class: 'n'}, brl(r.lucro)),
    el('td', {class: 'n'}, pct(r.margem)),
    el('td', {class: 'n', title: r.meta_origem || ''}, pct(r.meta), el('div', {class: 'mlbs'}, r.meta_origem || '')),
    celulaPreco(r));
  return tr;
}

// ---------- preço ----------
function anunciosMarcados() {
  const vistos = new Map();
  for (const r of LINHAS) if (SEL.has(r._i) && r.custo_unit && r.meta !== null && !vistos.has(chaveAnuncio(r)))
    vistos.set(chaveAnuncio(r), {mlb: r.mlb, modelo: r.modelo, custo: r.custo_unit, meta: r.meta, sku: r.sku, imposto_pct: r.imposto_pct});
  return [...vistos.values()];
}

async function calcularPrecos() {
  const itens = anunciosMarcados();
  $('sCalcular').disabled = true; mensagem('');
  try {
    for (let i = 0; i < itens.length; i += 6) {
      progresso('Calculando o preço necessário… ' + i + ' de ' + itens.length, 10 + 90 * i / itens.length);
      const d = await json('/api/shopee/preco-alvo', {parametros: parametros(), itens: itens.slice(i, i + 6)});
      for (const x of d.resultados) PRECO[x.mlb + '|' + x.modelo] = x;
    }
    progresso(null); desenhar();
  } catch (e) { progresso(null); mensagem(e.message, 'erro'); desenhar(); }
}

function abrirPainel() {
  const itens = anunciosMarcados().filter(x => (PRECO[x.mlb + '|' + x.modelo] || {}).acao === 'subir');
  PAINEL.itens = itens.slice(0, MAX_APLICAR);
  $('sPrecoLista').replaceChildren(...PAINEL.itens.map(x => {
    const p = PRECO[x.mlb + '|' + x.modelo];
    return el('tr', {}, el('td', {}, x.mlb + (x.modelo !== '0' ? ' / ' + x.modelo : '')), el('td', {}, x.sku), el('td', {class: 'n'}, brl(p.preco_atual)),
      el('td', {class: 'n'}, brl(p.preco_novo)), el('td', {class: 'n'}, '+' + pct(p.variacao_pct)), el('td', {class: 'n'}, pct(p.margem_nova)));
  }));
  $('sLiberarCx').hidden = !PAINEL.itens.some(x => PRECO[x.mlb + '|' + x.modelo].requer_liberacao);
  $('sLiberar').checked = false; $('sEntendi').checked = false; $('sPrecoOk').disabled = true;
  const res = $('sPrecoRes');
  res.hidden = itens.length <= MAX_APLICAR; res.className = 'aviso';
  res.textContent = itens.length > MAX_APLICAR ? 'Vão só os primeiros ' + MAX_APLICAR + ' por vez. Depois de confirmar, clique de novo para os outros.' : '';
  PAINEL.hidden = false; PAINEL.scrollIntoView({behavior: 'smooth', block: 'center'});
}

async function aplicar() {
  const itens = PAINEL.itens || [];
  $('sPrecoOk').disabled = true;
  try {
    const d = await json('/api/shopee/preco-aplicar', {confirmacao: CONFIRMACAO, parametros: parametros(), liberar_acima_limite: $('sLiberar').checked,
      itens: itens.map(x => { const p = PRECO[x.mlb + '|' + x.modelo]; return Object.assign({}, x, {preco_atual_visto: p.preco_atual, preco_novo_visto: p.preco_novo}); })});
    const ok = d.resultados.filter(r => r.status === 'OK');
    for (const r of d.resultados) {
      const k = r.mlb + '|' + r.modelo;
      if (r.status === 'OK') PRECO[k] = Object.assign({}, PRECO[k], {acao: 'ok', preco_atual: r.preco_novo, mensagem: r.mensagem});
    }
    const res = $('sPrecoRes'); res.hidden = false; res.className = 'aviso' + (ok.length === d.resultados.length ? ' ok' : '');
    res.textContent = d.resultados.map(r => r.mlb + ': ' + (r.status === 'OK' ? '✔ ' : r.status === 'PULADO' ? '— ' : '✖ ') + r.mensagem).join(' | ');
    desenhar();
  } catch (e) {
    const res = $('sPrecoRes'); res.hidden = false; res.className = 'aviso erro'; res.textContent = e.message;
  } finally { $('sEntendi').checked = false; }
}

// ---------- eventos ----------
$('sBuscar').onclick = buscar;
$('sBusca').oninput = e => { busca = e.target.value; desenhar(); };
let espera = null;
for (const id of ['sImposto', 'sMargem']) $(id).oninput = () => { clearTimeout(espera); espera = setTimeout(() => calcular().catch(e => mensagem(e.message, 'erro')), 400); };
$('sMarcarRuins').onclick = () => { visiveis().filter(r => r.classe === 'abaixo' || r.classe === 'prejuizo').forEach(r => SEL.add(r._i)); desenhar(); };
$('sLimpar').onclick = () => { SEL.clear(); desenhar(); };
$('sCalcular').onclick = calcularPrecos;
$('sPreco').onclick = abrirPainel;
$('sEntendi').onchange = e => { $('sPrecoOk').disabled = !e.target.checked; };
$('sPrecoOk').onclick = aplicar;
$('sPrecoCancela').onclick = () => { PAINEL.hidden = true; };
