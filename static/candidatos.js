"use strict";
const $ = id => document.getElementById(id);
const el = (tag, props, ...filhos) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) { if (k === 'class') e.className = v; else e[k] = v; }
  for (const f of filhos) if (f !== null && f !== undefined) e.append(f instanceof Node ? f : String(f));
  return e;
};
const num = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR');
const dec = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR', {minimumFractionDigits: 1, maximumFractionDigits: 1});
const brl = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR', {style:'currency', currency:'BRL'});

let BASE = [];               // um registro por anúncio (conta + MLB)
let DIAS = 30, PERIODO = '';
let ordem = {col: 'unidades', desc: true};

function mensagem(texto, tipo){ const m = $('cMsg'); m.textContent = texto || ''; m.hidden = !texto; m.className = 'aviso' + (tipo ? ' ' + tipo : ''); }
function progresso(texto, pct){
  $('cProg').hidden = texto === null; if (texto === null) return;
  $('cBarra').style.width = Math.max(2, Math.min(100, pct || 0)) + '%'; $('cTxt').textContent = texto;
}
async function json(url, corpo){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(corpo)});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sessão expirada'); }
  let d = null; try { d = await r.json(); } catch(e) {}
  if (!r.ok || !d || d.ok === false) throw new Error((d && d.error) || ('Erro ' + r.status + '. O servidor pode estar acordando; tente de novo em 1 minuto.'));
  return d;
}
async function comTentativas(fn, n = 3){
  let ultimo;
  for (let i = 0; i < n; i++) { try { return await fn(); } catch (e) { ultimo = e; if (/Sessão expirada/.test(e.message)) throw e; await new Promise(r => setTimeout(r, 1200 * (i + 1))); } }
  throw ultimo;
}
async function emParalelo(tarefas, limite, aoConcluir){
  let i = 0, feitas = 0;
  const trabalhador = async () => { while (i < tarefas.length) { const t = tarefas[i++]; await t(); aoConcluir && aoConcluir(++feitas, tarefas.length); } };
  await Promise.all(Array.from({length: Math.min(limite, tarefas.length)}, trabalhador));
}
const dataISO = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');

// ---------- busca ----------
async function buscar(){
  const contas = $('cConta').value === 'AMBAS' ? ['ML19', 'ML44'] : [$('cConta').value];
  DIAS = Number($('cDias').value);
  const hoje = new Date(), fim = dataISO(hoje), ini = new Date(hoje); ini.setDate(ini.getDate() - (DIAS - 1));
  PERIODO = dataISO(ini) + ' a ' + fim + ' (' + DIAS + ' dias)';
  const mapa = new Map();   // conta|mlb -> registro
  const janelas = [];       // blocos de 3 dias, para reduzir chamadas e manter páginas pequenas
  for (let d = new Date(ini); dataISO(d) <= fim; d.setDate(d.getDate() + 3)) {
    const a = new Date(d), b = new Date(d); b.setDate(b.getDate() + 2); janelas.push([dataISO(a), dataISO(b) > fim ? fim : dataISO(b)]);
  }
  $('cBuscar').disabled = true; $('cResultado').hidden = true; mensagem('');
  try {
    // 1) pedidos
    const tarefas = [];
    for (const conta of contas) for (const [de, ate] of janelas) tarefas.push(async () => {
      let off = 0;
      for (let guarda = 0; guarda < 400; guarda++) {
        const d = await comTentativas(() => json('/api/candidatos/pedidos', {conta, de, ate, offset: off}));
        for (const it of d.itens) {
          const k = conta + '|' + it.mlb; let r = mapa.get(k);
          if (!r) { r = {conta, mlb: it.mlb, titulo: it.titulo, sku: it.sku, unidades: 0, pedidos: 0, valor: 0}; mapa.set(k, r); }
          r.unidades += it.unidades; r.pedidos += it.pedidos; r.valor += it.valor; if (!r.sku && it.sku) r.sku = it.sku; if (!r.titulo && it.titulo) r.titulo = it.titulo;
        }
        if (d.concluido) break; off = d.proximo;
      }
    });
    await emParalelo(tarefas, 3, (f, t) => progresso('Lendo pedidos… ' + f + ' de ' + t + ' blocos', Math.round(f / t * 60)));
    if (!mapa.size) { progresso(null); mensagem('Nenhum pedido pago encontrado neste período.', 'erro'); return; }
    // 2) tipo de envio de cada anúncio
    const lotes = [];
    for (const conta of contas) {
      const ids = [...mapa.values()].filter(r => r.conta === conta).map(r => r.mlb);
      for (let i = 0; i < ids.length; i += 20) lotes.push(async () => {
        const d = await comTentativas(() => json('/api/candidatos/itens', {conta, ids: ids.slice(i, i + 20)}));
        for (const [mlb, inf] of Object.entries(d.itens)) { const r = mapa.get(conta + '|' + mlb); if (r) Object.assign(r, {info: inf}); }
      });
    }
    await emParalelo(lotes, 3, (f, t) => progresso('Lendo os anúncios… ' + f + ' de ' + t, 60 + Math.round(f / t * 25)));
    const todos = [...mapa.values()], semInfo = todos.filter(r => !r.info).length, jaFull = todos.filter(r => r.info && r.info.logistica === 'fulfillment').length;
    BASE = todos.filter(r => !(r.info && r.info.logistica === 'fulfillment'));
    for (const r of BASE) { if (!r.sku && r.info && r.info.sku) r.sku = r.info.sku; }
    // 3) custos
    let custos = {}, avisoCusto = '';
    const skus = [...new Set(BASE.map(r => r.sku).filter(s => s && s.length <= 80))];
    try {
      const blocos = []; for (let i = 0; i < skus.length; i += 100) blocos.push(skus.slice(i, i + 100));
      await emParalelo(blocos.map(b => async () => { const d = await comTentativas(() => json('/api/envios/custos', {skus: b})); Object.assign(custos, d.custos); }), 2,
        (f, t) => progresso('Buscando custos… ' + f + ' de ' + t, 85 + Math.round(f / t * 14)));
    } catch (e) { avisoCusto = ' Não consegui buscar os custos (' + e.message + '); a coluna de custo ficou vazia.'; }
    for (const r of BASE) r.custo = r.sku && custos[r.sku] !== undefined ? custos[r.sku] : null;
    progresso(null);
    $('cResultado').hidden = false; desenhar();
    mensagem(BASE.length + ' anúncios vendidos fora do Full. ' + (jaFull ? jaFull + ' já são Full e foram ocultados. ' : '') + (semInfo ? semInfo + ' anúncios não puderam ser lidos (aparecem sem tipo de envio). ' : '') + avisoCusto.trim(), avisoCusto || semInfo ? 'erro' : 'ok');
  } catch (e) { progresso(null); mensagem('Não consegui terminar: ' + e.message, 'erro'); }
  $('cBuscar').disabled = false;
}

// ---------- tabela ----------
const COLUNAS = [
  ['conta', 'Conta', false], ['sku', 'SKU', false], ['produto', 'Produto', false], ['envio', 'Envio atual', false], ['unidades', 'Unidades', true], ['pedidos', 'Pedidos', true],
  ['valor', 'Faturamento', true], ['preco_medio', 'Preço médio', true], ['por_dia', 'Vendas/dia', true], ['custo', 'Custo unit.', true], ['custo_total', 'Custo total', true], ['estoque', 'Estoque anúncio', true]];
function linhasBase(){
  const porSku = $('cSku').checked;
  const mk = (r, extra) => ({conta: r.conta, sku: r.sku || '', produto: r.titulo, mlbs: r.mlb, envio: r.info ? r.info.rotulo : '—', situacao: r.info ? (r.info.status === 'active' ? 'Ativo' : 'Não ativo') : '—',
    unidades: r.unidades, pedidos: r.pedidos, valor: r.valor, custo: r.custo, estoque: r.info ? r.info.estoque : null, ...extra});
  let lista;
  if (!porSku) lista = BASE.map(r => mk(r, {}));
  else {
    const m = new Map();
    for (const r of BASE) {
      const k = r.conta + '|' + (r.sku || r.mlb);
      const g = m.get(k);
      if (!g) m.set(k, mk(r, {n: 1}));
      else {
        g.unidades += r.unidades; g.pedidos += r.pedidos; g.valor += r.valor; g.mlbs += ', ' + r.mlb; g.n++;
        if (r.info && r.info.status === 'active') g.situacao = 'Ativo';
        g.estoque = (g.estoque === null || r.info === undefined || r.info.estoque === null) ? g.estoque : g.estoque + r.info.estoque;
        if (g.envio !== (r.info ? r.info.rotulo : '—')) g.envio = 'Vários';
      }
    }
    lista = [...m.values()]; lista.forEach(g => { if (g.n > 1) g.produto = g.produto + ' (+' + (g.n - 1) + ' anúncios)'; });
  }
  for (const g of lista) {
    g.preco_medio = g.unidades ? g.valor / g.unidades : null; g.por_dia = g.unidades / DIAS;
    g.custo_total = g.custo === null || g.custo === undefined ? null : g.custo * g.unidades;
  }
  return lista;
}
function filtradas(){
  const q = $('cBusca').value.trim().toLowerCase(), min = Math.max(1, Number($('cMin').value) || 1), ativos = $('cAtivos').checked, conta = $('cFiltroConta').value;
  const l = linhasBase().filter(r => r.unidades >= min && (!ativos || r.situacao !== 'Não ativo') && (!conta || r.conta === conta) &&
    (!q || [r.sku, r.produto, r.mlbs].join(' ').toLowerCase().includes(q)));
  const c = ordem.col, f = ordem.desc ? -1 : 1;
  return l.sort((a, b) => {
    const x = a[c], y = b[c];
    if (x === null || x === undefined) return (y === null || y === undefined) ? 0 : 1;
    if (y === null || y === undefined) return -1;
    return (typeof x === 'string' ? x.localeCompare(y, 'pt-BR') : x - y) * f;
  });
}
function desenhar(){
  const cab = $('cCab'); cab.replaceChildren(...COLUNAS.map(([k, nome, n]) => {
    const th = el('th', {class: (n ? 'n ' : '') + 'ord', title: 'Clique para ordenar'}, nome + (ordem.col === k ? (ordem.desc ? ' ▼' : ' ▲') : ''));
    th.onclick = () => { if (ordem.col === k) ordem.desc = !ordem.desc; else { ordem.col = k; ordem.desc = n; } desenhar(); };
    return th;
  }));
  const l = filtradas(), corpo = $('cCorpo'); corpo.replaceChildren();
  for (const r of l.slice(0, 1500)) corpo.append(el('tr', {},
    el('td', {}, r.conta), el('td', {}, r.sku), el('td', {title: r.mlbs}, r.produto.slice(0, 80), el('div', {class: 'mlbs'}, r.mlbs.length > 40 ? r.mlbs.slice(0, 40) + '…' : r.mlbs)),
    el('td', {}, r.envio), el('td', {class: 'n'}, num(r.unidades)), el('td', {class: 'n'}, num(r.pedidos)), el('td', {class: 'n'}, brl(r.valor)), el('td', {class: 'n'}, brl(r.preco_medio)),
    el('td', {class: 'n'}, dec(r.por_dia)), el('td', {class: 'n'}, brl(r.custo)), el('td', {class: 'n'}, brl(r.custo_total)), el('td', {class: 'n'}, num(r.estoque))));
  $('cVazio').hidden = l.length > 0;
  const un = l.reduce((s, r) => s + r.unidades, 0), fat = l.reduce((s, r) => s + r.valor, 0);
  $('cCartoes').replaceChildren(
    el('div', {class: 'card destaque'}, el('b', {}, num(l.length)), el('span', {}, $('cSku').checked ? 'SKUs na lista' : 'anúncios na lista')),
    el('div', {class: 'card'}, el('b', {}, num(un)), el('span', {}, 'unidades vendidas (' + PERIODO + ')')),
    el('div', {class: 'card'}, el('b', {}, brl(fat)), el('span', {}, 'faturamento')),
    el('div', {class: 'card'}, el('b', {}, num(l.filter(r => r.custo === null || r.custo === undefined).length)), el('span', {}, 'sem custo na planilha')));
  if (l.length > 1500) mensagem('Mostrando os 1.500 primeiros da ordem escolhida. A planilha baixada leva todos os ' + num(l.length) + '.', 'ok');
}
for (const id of ['cMin', 'cSku', 'cAtivos', 'cFiltroConta']) $(id).onchange = desenhar;
$('cMin').oninput = desenhar; $('cBusca').oninput = desenhar;
$('cBuscar').onclick = buscar;
$('cBaixar').onclick = async () => {
  const b = $('cBaixar'); b.disabled = true;
  try {
    const resp = await fetch('/api/candidatos/exportar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({linhas: filtradas(), periodo: PERIODO})});
    if (resp.status === 401) { location.href = '/login'; return; }
    if (!resp.ok) { let m = 'Erro ' + resp.status; try { m = (await resp.json()).error || m; } catch(e) {} throw new Error(m); }
    const blob = await resp.blob(), a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'candidatos_full.xlsx';
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (e) { mensagem('Não consegui gerar a planilha: ' + e.message, 'erro'); }
  b.disabled = false;
};
