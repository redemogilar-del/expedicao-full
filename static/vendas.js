"use strict";
const $ = id => document.getElementById(id);
const el = (tag, props, ...filhos) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) { if (k === 'class') e.className = v; else e[k] = v; }
  for (const f of filhos) if (f !== null && f !== undefined) e.append(f instanceof Node ? f : String(f));
  return e;
};
const num = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR');
const brl = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR', {style:'currency', currency:'BRL'});
const pct = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString('pt-BR', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + '%';
const ROT = {prejuizo: 'Prejuízo', abaixo: 'Abaixo da margem', dentro: 'Dentro da margem', sem_dados: 'Sem dados'};
const ENV = {a_enviar: 'A enviar', enviado: 'Já enviado', full: 'Full'};

let RAW = [], CAD = {}, LINHAS = [], MARCA = new Set(), PERIODO = '', DIA = '';
let grupo = 'a_enviar', classe = 'todas', busca = '';

function mensagem(texto, tipo){ const m = $('vMsg'); m.textContent = texto || ''; m.hidden = !texto; m.className = 'aviso' + (tipo ? ' ' + tipo : ''); }
function progresso(texto, p){
  $('vProg').hidden = texto === null; if (texto === null) return;
  $('vBarra').style.width = Math.max(2, Math.min(100, p || 0)) + '%'; $('vTxt').textContent = texto;
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
const parametros = () => ({imposto_pct: $('vImposto').value, margem_padrao: $('vMargem').value});

// ---------- busca ----------
async function buscar(){
  const contas = $('vConta').value === 'AMBAS' ? ['ML19', 'ML44'] : [$('vConta').value], de = $('vDe').value, ate = $('vAte').value;
  if (!de || !ate || ate < de) { mensagem('Confira as datas: "Até" não pode ser antes de "De".', 'erro'); return; }
  $('vBuscar').disabled = true; mensagem(''); $('vResultado').hidden = true;
  try {
    RAW = []; MARCA = new Set();
    for (const conta of contas) {
      let off = 0, total = 0;
      for (;;) {
        progresso(conta + ': lendo pedidos' + (total ? ' (' + Math.min(off, total) + ' de ' + total + ')' : '…'), total ? 5 + 55 * off / total / contas.length : 5);
        const d = await comTentativas(() => json('/api/vendas/pedidos', {conta, de, ate, offset: off}));
        RAW.push(...d.itens); total = d.total; off = d.proximo;
        if (d.concluido) break;
      }
    }
    if (!RAW.length) { progresso(null); mensagem('Nenhuma venda paga neste período.', ''); $('vBuscar').disabled = false; return; }
    const skus = [...new Set(RAW.map(r => r.sku).filter(Boolean))]; CAD = {};
    for (let i = 0; i < skus.length; i += 100) {
      progresso('Lendo custos e margens na planilha (' + Math.min(i + 100, skus.length) + ' de ' + skus.length + ' SKUs)…', 60 + 30 * i / skus.length);
      Object.assign(CAD, (await comTentativas(() => json('/api/vendas/cadastro', {skus: skus.slice(i, i + 100)}))).cadastro);
    }
    PERIODO = de === ate ? de.split('-').reverse().join('/') : de.split('-').reverse().join('/') + ' a ' + ate.split('-').reverse().join('/'); DIA = ate;
    progresso('Calculando o lucro…', 95);
    await recalcular();
    grupo = 'a_enviar'; classe = 'todas'; busca = ''; $('vBusca').value = '';
    if (!LINHAS.some(r => r.grupo === 'a_enviar')) grupo = 'todos';
    $('vResultado').hidden = false; desenhar(); progresso(null);
    $('vResultado').scrollIntoView({behavior: 'smooth'});
  } catch (err) { progresso(null); mensagem(err.message, 'erro'); }
  $('vBuscar').disabled = false;
}
async function recalcular(){
  const d = await json('/api/vendas/calcular', {linhas: RAW, cadastro: CAD, parametros: parametros()});
  LINHAS = d.linhas.map((r, i) => Object.assign(r, {_i: i}));
}
let temporizador = null;
for (const id of ['vImposto', 'vMargem']) $(id).oninput = () => {
  if (!RAW.length) return;
  clearTimeout(temporizador);
  temporizador = setTimeout(async () => { try { mensagem(''); await recalcular(); desenhar(); } catch (e) { mensagem(e.message, 'erro'); } }, 500);
};

// ---------- tela ----------
const noGrupo = r => grupo === 'todos' || r.grupo === grupo;
const podeMarcar = r => r.grupo === 'a_enviar';
function visiveis(){
  const q = busca.trim().toLowerCase();
  return LINHAS.filter(r => noGrupo(r) && (classe === 'todas' || r.classe === classe) &&
    (!q || [r.pedido, r.sku, r.titulo, r.mlb].join(' ').toLowerCase().includes(q)));
}
function desenhar(){
  const esc = LINHAS.filter(noGrupo), c = {prejuizo: 0, abaixo: 0, dentro: 0, sem_dados: 0}; let lucro = 0, venda = 0, prej = 0, deixou = 0;
  esc.forEach(r => { c[r.classe]++; if (r.classe !== 'sem_dados') { lucro += r.lucro; venda += r.venda; if (r.perda_tipo === 'prejuizo') prej += r.perda; else if (r.perda_tipo === 'deixou') deixou += r.perda; } });
  const marcados = LINHAS.filter(r => MARCA.has(r._i));
  $('vCartoes').replaceChildren(
    el('div', {class:'card'}, el('b', {}, num(esc.length)), el('span', {}, 'itens vendidos (nesta visão)')),
    el('div', {class:'card destaque'}, el('b', {}, num(c.dentro)), el('span', {}, 'dentro da margem')),
    el('div', {class:'card'}, el('b', {}, num(c.abaixo)), el('span', {}, 'abaixo da margem · deixou de ganhar ' + brl(deixou))),
    el('div', {class:'card risco'}, el('b', {}, num(c.prejuizo)), el('span', {}, 'com prejuízo · perdeu ' + brl(prej))),
    el('div', {class:'card'}, el('b', {}, num(c.sem_dados)), el('span', {}, 'sem dados (custo, tarifa ou frete)')),
    el('div', {class:'card'}, el('b', {}, brl(lucro)), el('span', {}, 'lucro de ' + brl(venda) + ' vendidos (só com dados)')),
    el('div', {class:'card risco'}, el('b', {}, num(marcados.length)), el('span', {}, 'marcados para não enviar')));
  const gb = $('vGrupos'); gb.replaceChildren();
  for (const [k, nome] of [['a_enviar', 'A enviar (não Full)'], ['enviado', 'Já enviados'], ['full', 'Full'], ['todos', 'Todos']])
    gb.append(el('button', {class: k === grupo ? 'on' : '', onclick: () => { grupo = k; desenhar(); }}, nome + ' (' + (k === 'todos' ? LINHAS.length : LINHAS.filter(r => r.grupo === k).length) + ')'));
  const cb = $('vClasses'); cb.replaceChildren();
  for (const [k, nome] of [['todas', 'Todas'], ['prejuizo', 'Prejuízo'], ['abaixo', 'Abaixo da margem'], ['dentro', 'Dentro da margem'], ['sem_dados', 'Sem dados']])
    cb.append(el('button', {class: k === classe ? 'on' : '', onclick: () => { classe = k; desenhar(); }}, nome + ' (' + (k === 'todas' ? esc.length : (c[k] || 0)) + ')'));
  const lista = visiveis(), corpo = $('vCorpo'); corpo.replaceChildren(...lista.map(linha));
  $('vVazio').hidden = lista.length > 0;
  const marc = lista.filter(podeMarcar); $('vTodos').checked = marc.length > 0 && marc.every(r => MARCA.has(r._i)); $('vTodos').disabled = !marc.length;
  $('vBaixarNao').disabled = !marcados.length; $('vBaixarNao').textContent = 'Baixar lista “Não enviar” (' + marcados.length + ')';
  $('vMarcarRuins').disabled = !lista.some(r => podeMarcar(r) && (r.classe === 'abaixo' || r.classe === 'prejuizo'));
}
function linha(r){
  const cb = el('input', {type:'checkbox', checked: MARCA.has(r._i), disabled: !podeMarcar(r), title: podeMarcar(r) ? 'Não enviar este pedido' : 'Só dá para marcar o que ainda será enviado por você'});
  cb.onchange = () => { if (cb.checked) MARCA.add(r._i); else MARCA.delete(r._i); desenhar(); };
  const sinal = v => v === null || v === undefined ? '' : (v < 0 ? 'n neg' : 'n pos');
  return el('tr', {class: MARCA.has(r._i) ? 'naoenviar' : ''},
    el('td', {class:'chk'}, cb),
    el('td', {}, el('span', {class:'tag v-' + r.classe, title: r.pendencias ? 'Falta: ' + r.pendencias : (r.meta_origem === 'padrão' ? 'Usou a margem mínima padrão (produto sem margem na planilha)' : '')}, ROT[r.classe])),
    el('td', {class: 'n ' + (r.perda_tipo === 'prejuizo' ? 'neg' : '')}, r.perda ? el('span', {}, brl(r.perda), el('div', {class:'mlbs'}, r.perda_tipo === 'prejuizo' ? 'prejuízo' : 'deixou de ganhar')) : '—'),
    el('td', {}, r.conta), el('td', {}, r.pedido, el('div', {class:'mlbs'}, r.data)), el('td', {}, r.sku || '—'), el('td', {}, el('span', {class:'tag e-' + r.grupo, title: r.status_envio ? r.status_envio + (r.substatus ? ' / ' + r.substatus : '') : ''}, r.status_rotulo || '—')), el('td', {}, r.titulo.slice(0, 70), el('div', {class:'mlbs'}, r.mlb + (r.obs ? ' · ' + r.obs : ''))),
    el('td', {class:'n'}, num(r.quantidade)), el('td', {class:'n'}, brl(r.venda)), el('td', {class:'n'}, brl(r.tarifa)), el('td', {class:'n'}, brl(r.frete)),
    el('td', {class:'n'}, brl(r.imposto)), el('td', {class:'n'}, brl(r.custo_total)), el('td', {class: sinal(r.lucro)}, brl(r.lucro)), el('td', {class: sinal(r.margem)}, pct(r.margem)),
    el('td', {class:'n', title: r.meta_origem === 'padrão' ? 'margem padrão' : 'margem da planilha'}, pct(r.meta) + (r.meta_origem === 'padrão' ? '*' : '')), el('td', {}, ENV[r.grupo] || r.grupo));
}
$('vBusca').oninput = e => { busca = e.target.value; desenhar(); };
$('vTodos').onchange = e => { visiveis().filter(podeMarcar).forEach(r => { if (e.target.checked) MARCA.add(r._i); else MARCA.delete(r._i); }); desenhar(); };
$('vMarcarRuins').onclick = () => {
  const a = visiveis().filter(r => podeMarcar(r) && (r.classe === 'abaixo' || r.classe === 'prejuizo')); a.forEach(r => MARCA.add(r._i)); desenhar();
  mensagem(a.length + ' vendas marcadas para não enviar. Desmarque na lista as que quiser enviar mesmo assim.', 'ok');
};
$('vLimpar').onclick = () => { MARCA = new Set(); desenhar(); mensagem(''); };

// ---------- relatórios ----------
const corpoRelatorio = tipo => ({tipo, dia: DIA, periodo: PERIODO, parametros: parametros(),
  linhas: LINHAS.map(r => ({conta: r.conta, pedido: r.pedido, data: r.data, mlb: r.mlb, sku: r.sku, titulo: r.titulo, grupo: r.grupo, logistica: r.logistica, status_envio: r.status_envio, obs: r.obs,
    meta_origem: r.meta_origem, quantidade: r.quantidade, venda: r.venda, tarifa: r.tarifa, frete: r.frete, custo_unit: r.custo_unit, meta: r.meta, nao_enviar: MARCA.has(r._i)}))});
async function baixar(tipo, botao){
  botao.disabled = true;
  try {
    const resp = await fetch('/api/vendas/exportar', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(corpoRelatorio(tipo))});
    if (resp.status === 401) { location.href = '/login'; return; }
    if (!resp.ok) { let m = 'Erro ' + resp.status; try { m = (await resp.json()).error || m; } catch(e) {} throw new Error(m); }
    const blob = await resp.blob(), nome = (resp.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)/);
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = nome ? decodeURIComponent(nome[1]) : 'vendas.xlsx';
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    mensagem('Planilha baixada.', 'ok');
  } catch (err) { mensagem('Não consegui gerar a planilha: ' + err.message, 'erro'); }
  botao.disabled = false; desenhar();
}
$('vBaixarNao').onclick = () => baixar('nao_enviar', $('vBaixarNao'));
$('vBaixarDia').onclick = () => baixar('dia', $('vBaixarDia'));
$('vSalvar').onclick = async () => {
  const b = $('vSalvar'); b.disabled = true; mensagem('Salvando na planilha do Google…');
  try {
    const d = await json('/api/vendas/salvar', corpoRelatorio('salvar'));
    mensagem('Relatório salvo na planilha: ' + d.abas.join(', ') + '.', 'ok');
  } catch (err) { mensagem(err.message, 'erro'); }
  b.disabled = false;
};
$('vBuscar').onclick = buscar;
