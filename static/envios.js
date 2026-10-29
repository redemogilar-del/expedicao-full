"use strict";
const $ = id => document.getElementById(id);
const el = (tag, props, ...filhos) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) { if (k === 'class') e.className = v; else e[k] = v; }
  for (const f of filhos) if (f !== null && f !== undefined) e.append(f instanceof Node ? f : String(f));
  return e;
};
const ROTULOS = {enviar:'Enviar', frete_alto:'Frete alto', sem_dados:'Sem frete/custo', impedido:'Impedido pelo ML', nao_precisa:'Não precisa'};
const num = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR');
const brl = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR', {style:'currency', currency:'BRL'});

let BASE = null;          // {itens, linhas, data, nome}
let ARQ = null;           // arquivo do relatório
let LINHAS = [];          // resultado do cálculo, com campos de tela: incluir, qtd, qtd_calc
let ARQUIVO = '', PARAMS = {};
let filtro = 'enviar', busca = '';

function mensagem(texto, tipo){ const m = $('msg'); m.textContent = texto || ''; m.hidden = !texto; m.className = 'aviso' + (tipo ? ' ' + tipo : ''); }

async function postar(url, corpo){
  const r = await fetch(url, {method:'POST', body: corpo});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sessão expirada'); }
  let d = null; try { d = await r.json(); } catch(e) {}
  if (!r.ok || !d || d.ok === false) throw new Error((d && d.error) || ('Erro ' + r.status + '. O servidor pode estar acordando; tente de novo em 1 minuto.'));
  return d;
}

// ---------- base de frete e custo (guardada neste navegador) ----------
function salvarBase(){ try { localStorage.setItem('exp_base', JSON.stringify(BASE)); } catch(e) {} }
function carregarBase(){ try { BASE = JSON.parse(localStorage.getItem('exp_base') || 'null'); } catch(e) { BASE = null; } }
function mostrarBase(){
  const info = $('baseInfo');
  if (!BASE) { info.textContent = 'Nenhuma base enviada ainda.'; $('txtBotBase').textContent = 'Enviar a base'; }
  else {
    const dias = Math.floor((Date.now() - new Date(BASE.data).getTime()) / 86400000);
    info.textContent = 'Base: ' + BASE.nome + ' — ' + BASE.linhas + ' produtos, enviada ' + (dias <= 0 ? 'hoje' : 'há ' + dias + ' dia(s)') + '.' + (dias > 14 ? ' Está antiga: confira o frete e o custo.' : '');
    $('txtBotBase').textContent = 'Trocar a base';
  }
  info.classList.toggle('ok', !!BASE);
  atualizarBotao();
}
function atualizarBotao(){ $('calcular').disabled = !(ARQ && BASE); }

$('arqBase').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  mensagem('Lendo a base…');
  try {
    const fd = new FormData(); fd.append('arquivo', f);
    const d = await postar('/api/envios/base', fd);
    BASE = {itens: d.itens, linhas: d.linhas, nome: d.nome, data: new Date().toISOString()};
    salvarBase(); mostrarBase(); mensagem(d.sem_valor ? d.sem_valor + ' produtos da base estão sem frete ou custo e ficarão de fora.' : '', d.sem_valor ? '' : null);
  } catch (err) { mensagem(err.message, 'erro'); }
  e.target.value = '';
};

// ---------- relatório do ML ----------
function escolherRelatorio(f){
  if (!f) return;
  ARQ = f; $('txtRel').textContent = '✓ ' + f.name; $('soltarRel').classList.add('ok'); atualizarBotao();
}
$('arqRel').onchange = e => escolherRelatorio(e.target.files[0]);
const alvo = $('soltarRel');
['dragenter', 'dragover'].forEach(ev => alvo.addEventListener(ev, e => { e.preventDefault(); alvo.classList.add('sobre'); }));
['dragleave', 'drop'].forEach(ev => alvo.addEventListener(ev, e => { e.preventDefault(); alvo.classList.remove('sobre'); }));
alvo.addEventListener('drop', e => escolherRelatorio(e.dataTransfer.files[0]));

// ---------- cálculo ----------
$('calcular').onclick = async () => {
  mensagem('Calculando…'); $('calcular').disabled = true;
  try {
    const fd = new FormData();
    fd.append('relatorio', ARQ); fd.append('base', JSON.stringify(BASE.itens));
    fd.append('meses', $('pMeses').value); fd.append('limite_frete_pct', $('pLimite').value); fd.append('qtd_teste', $('pTeste').value);
    const d = await postar('/api/envios/calcular', fd);
    ARQUIVO = d.arquivo; PARAMS = d.parametros;
    LINHAS = d.linhas.map(r => ({...r, qtd_calc: r.qtd, incluir: r.status === 'enviar' && r.tipo !== 'Teste'}));
    filtro = 'enviar'; busca = ''; $('busca').value = '';
    mensagem(''); $('resultado').hidden = false; desenhar();
    $('resultado').scrollIntoView({behavior:'smooth'});
  } catch (err) { mensagem(err.message, 'erro'); }
  atualizarBotao();
};

// ---------- tela de resultado ----------
const podeMarcar = r => (r.status === 'enviar' || r.status === 'frete_alto' || r.status === 'sem_dados') && r.qtd_calc > 0;
function visiveis(){
  const q = busca.trim().toLowerCase();
  return LINHAS.filter(r => (filtro === 'todos' || r.status === filtro || (filtro === 'marcados' && r.incluir)) &&
    (!q || [r.sku, r.produto, r.codigo_ml, r.mlbu, ...(r.mlbs || [])].join(' ').toLowerCase().includes(q)));
}
function resumoSku(){
  const m = new Map();
  for (const r of LINHAS) if (r.incluir && r.qtd > 0) { const t = m.get(r.sku) || {sku: r.sku || '(sem SKU)', qtd: 0}; t.qtd += r.qtd; m.set(r.sku, t); }
  return [...m.values()].sort((a, b) => a.sku.localeCompare(b.sku));
}
function desenhar(){
  const marc = LINHAS.filter(r => r.incluir && r.qtd > 0), un = marc.reduce((s, r) => s + r.qtd, 0), skus = resumoSku();
  const cont = {}; LINHAS.forEach(r => cont[r.status] = (cont[r.status] || 0) + 1);
  const testes = LINHAS.filter(r => r.status === 'enviar' && r.tipo === 'Teste');
  const cards = $('cartoes'); cards.replaceChildren(
    el('div', {class:'card destaque'}, el('b', {}, num(marc.length)), el('span', {}, 'produtos marcados para enviar')),
    el('div', {class:'card destaque'}, el('b', {}, num(un)), el('span', {}, 'unidades no total')),
    el('div', {class:'card'}, el('b', {}, num(skus.length)), el('span', {}, 'SKUs diferentes (produção)')),
    el('div', {class:'card'}, el('b', {}, num(cont.frete_alto || 0)), el('span', {}, 'barrados por frete alto')),
    el('div', {class:'card'}, el('b', {}, num(cont.sem_dados || 0)), el('span', {}, 'sem frete/custo na base')),
    el('div', {class:'card'}, el('b', {}, num(cont.nao_precisa || 0)), el('span', {}, 'já têm estoque suficiente')));
  const fb = $('filtros'); fb.replaceChildren();
  const abas = [['enviar', 'Enviar'], ['marcados', 'Marcados'], ['frete_alto', 'Frete alto'], ['sem_dados', 'Sem frete/custo'], ['impedido', 'Impedidos'], ['nao_precisa', 'Não precisa'], ['todos', 'Todos']];
  for (const [k, nome] of abas) {
    const n = k === 'todos' ? LINHAS.length : k === 'marcados' ? marc.length : (cont[k] || 0);
    fb.append(el('button', {class: k === filtro ? 'on' : '', onclick: () => { filtro = k; desenhar(); }}, nome + ' (' + n + ')'));
  }
  const pend = testes.filter(r => !r.incluir).length;
  $('barraTeste').hidden = !testes.length;
  $('avisoTeste').textContent = testes.length + ' produtos são envio-teste (sem estoque no Full e sem vendas). Eles começam desmarcados: marque os que o Mercado Livre sugeriu.' + (pend < testes.length ? ' ' + (testes.length - pend) + ' já marcados.' : '');
  const lista = visiveis(), corpo = $('corpo'); corpo.replaceChildren();
  for (const r of lista) corpo.append(linha(r));
  $('vazio').hidden = lista.length > 0;
  const cb = $('todos'); const marcaveis = lista.filter(podeMarcar); cb.checked = marcaveis.length > 0 && marcaveis.every(r => r.incluir); cb.disabled = !marcaveis.length;
  const cs = $('corpoSku'); cs.replaceChildren(...skus.map(t => el('tr', {}, el('td', {}, t.sku), el('td', {class:'n'}, num(t.qtd)))));
  $('totSku').textContent = num(un);
  $('baixarEnvio').disabled = $('baixarSku').disabled = marc.length === 0;
}
function linha(r){
  const cb = el('input', {type:'checkbox', checked: r.incluir, disabled: !podeMarcar(r)});
  cb.onchange = () => { r.incluir = cb.checked; desenhar(); };
  const q = el('input', {type:'number', min:0, max:100000, step:1, value: r.qtd, disabled: !r.incluir});
  q.onchange = () => { const v = Math.max(0, Math.min(100000, Math.round(Number(q.value) || 0))); r.qtd = v; if (v === 0) r.incluir = false; desenhar(); };
  const ml = r.mlbs && r.mlbs.length ? el('div', {class:'mlbs'}, r.mlbs.slice(0, 3).join(', ') + (r.mlbs.length > 3 ? ' +' + (r.mlbs.length - 3) : '')) : null;
  const imp = r.impacto_pct === null || r.impacto_pct === undefined ? '—' : brl(r.frete) + ' / ' + brl(r.custo) + ' = ' + r.impacto_pct.toLocaleString('pt-BR') + '%';
  return el('tr', {class: (r.incluir ? 'marcado' : '') + (!r.incluir && r.status !== 'enviar' ? ' fora' : '')},
    el('td', {class:'chk'}, cb), el('td', {}, el('span', {class:'tag t-' + r.status}, ROTULOS[r.status])),
    el('td', {}, r.sku), el('td', {}, r.produto.slice(0, 70), ml), el('td', {}, r.codigo_ml),
    el('td', {class:'n'}, num(r.vendas)), el('td', {class:'n', title:'Aptas + recebimento pendente'}, num(r.estoque_total)),
    el('td', {class:'n'}, r.cobertura_dias === null || r.cobertura_dias === undefined ? '—' : num(r.cobertura_dias)),
    el('td', {class:'n'}, imp), el('td', {class:'n'}, q), el('td', {}, r.tipo || ''), el('td', {}, r.motivo),
    el('td', {}, r.urgencia && r.urgencia !== '-' ? r.urgencia + (r.prazo && r.prazo !== '-' ? ' (' + r.prazo + ')' : '') : ''));
}
$('busca').oninput = e => { busca = e.target.value; desenhar(); };
$('todos').onchange = e => { visiveis().filter(podeMarcar).forEach(r => { r.incluir = e.target.checked; }); desenhar(); };
const testes = () => LINHAS.filter(r => r.status === 'enviar' && r.tipo === 'Teste');
$('marcarTestes').onclick = () => { testes().forEach(r => r.incluir = true); desenhar(); };
$('marcarTestesUrg').onclick = () => { testes().forEach(r => { r.incluir = r.urgencia === 'Urgente'; }); desenhar(); };
$('limparTestes').onclick = () => { testes().forEach(r => r.incluir = false); desenhar(); };

// ---------- downloads ----------
async function baixar(tipo){
  const botao = tipo === 'envio' ? $('baixarEnvio') : $('baixarSku'); botao.disabled = true;
  try {
    const linhas = LINHAS.map(r => ({incluir: r.incluir, qtd: r.qtd, qtd_calc: r.qtd_calc, status: r.status, tipo: r.tipo, codigo_ml: r.codigo_ml, mlbu: r.mlbu, sku: r.sku,
      produto: r.produto, mlbs: r.mlbs, motivo: r.motivo, vendas: r.vendas, estoque_total: r.estoque_total, frete: r.frete, custo: r.custo,
      impacto_pct: r.impacto_pct, urgencia: r.urgencia, prazo: r.prazo}));
    const resp = await fetch('/api/envios/exportar', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({tipo, linhas, parametros: PARAMS, arquivo: ARQUIVO})});
    if (resp.status === 401) { location.href = '/login'; return; }
    if (!resp.ok) { let m = 'Erro ' + resp.status; try { m = (await resp.json()).error || m; } catch(e) {} throw new Error(m); }
    const blob = await resp.blob(), nome = (resp.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)/);
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = nome ? decodeURIComponent(nome[1]) : 'envio_full.xlsx';
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    mensagem('Planilha baixada.', 'ok');
  } catch (err) { mensagem('Não consegui gerar a planilha: ' + err.message, 'erro'); }
  botao.disabled = false;
}
$('baixarEnvio').onclick = () => baixar('envio');
$('baixarSku').onclick = () => baixar('producao');

carregarBase(); mostrarBase();
