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

let ARQ = null;           // arquivo do relatório
let LINHAS = [];          // resultado do cálculo, com campos de tela: incluir, qtd, qtd_calc
let ARQUIVO = '', PARAMS = {};
let filtro = 'enviar', busca = '';
let SKUSEL = null;        // null = todos os SKUs; senão, Set dos SKUs escolhidos no filtro
const SEMSKU = '(Espaços em branco)';

function mensagem(texto, tipo){ const m = $('msg'); m.textContent = texto || ''; m.hidden = !texto; m.className = 'aviso' + (tipo ? ' ' + tipo : ''); }

async function postar(url, corpo){
  const r = await fetch(url, {method:'POST', body: corpo});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sessão expirada'); }
  let d = null; try { d = await r.json(); } catch(e) {}
  if (!r.ok || !d || d.ok === false) throw new Error((d && d.error) || ('Erro ' + r.status + '. O servidor pode estar acordando; tente de novo em 1 minuto.'));
  return d;
}

// ---------- relatório do ML ----------
function atualizarBotao(){ $('calcular').disabled = !ARQ; }
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
function progresso(texto, pct){ mensagem(texto + (pct === undefined ? '' : ' (' + pct + '%)')); }
async function json(url, corpo){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(corpo)});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sessão expirada'); }
  let d = null; try { d = await r.json(); } catch(e) {}
  if (!r.ok || !d || d.ok === false) throw new Error((d && d.error) || ('Erro ' + r.status + '. O servidor pode estar acordando; tente de novo em 1 minuto.'));
  return d;
}
function formParams(fd){ fd.append('meses', $('pMeses').value); fd.append('limite_frete_pct', $('pLimite').value); fd.append('qtd_teste', $('pTeste').value); return fd; }

$('calcular').onclick = async () => {
  $('calcular').disabled = true; $('resultado').hidden = true;
  try {
    const conta = $('pConta').value;
    progresso('Lendo o relatório…');
    const fd = formParams(new FormData()); fd.append('relatorio', ARQ);
    const lido = await postar('/api/envios/ler', fd);
    const base = {};
    for (const p of lido.pendencias) base[p.codigo_ml] = {frete: null, custo: null};

    // custo: planilha de custos, em blocos de 100 SKUs
    const skus = lido.skus, custo = {};
    for (let i = 0; i < skus.length; i += 100) {
      progresso('Buscando o custo na planilha… ' + Math.min(i + 100, skus.length) + ' de ' + skus.length + ' SKUs');
      Object.assign(custo, (await json('/api/envios/custos', {skus: skus.slice(i, i + 100)})).custos);
    }
    for (const p of lido.pendencias) base[p.codigo_ml].custo = custo[p.sku] ?? null;

    // frete: Mercado Livre, em blocos pequenos (cada produto consulta o anúncio)
    let fila = lido.pendencias.map(p => ({codigo_ml: p.codigo_ml, mlbs: p.mlbs})), feitos = 0, falhas = 0;
    const total = fila.length;
    while (fila.length) {
      progresso('Buscando o frete no Mercado Livre… ' + feitos + ' de ' + total + ' produtos', Math.round(feitos / Math.max(total, 1) * 100));
      const lote = fila.splice(0, 6);
      try {
        const d = await json('/api/envios/fretes', {conta, itens: lote});
        for (const [cod, r] of Object.entries(d.fretes)) { base[cod].frete = r.frete; if (r.erro) base[cod].erro = r.erro; }
        const pend = new Set(d.pendentes || []);
        feitos += lote.length - pend.size;
        if (pend.size) fila.push(...lote.filter(x => pend.has(x.codigo_ml)));
        falhas = 0;
      } catch (err) {
        fila.unshift(...lote);
        if (++falhas >= 3) throw err;
        await new Promise(r => setTimeout(r, 2500 * falhas));
      }
    }

    progresso('Calculando…');
    const fd2 = formParams(new FormData()); fd2.append('relatorio', ARQ); fd2.append('base', JSON.stringify(base));
    const d = await postar('/api/envios/calcular', fd2);
    ARQUIVO = d.arquivo; PARAMS = {...d.parametros, conta};
    SKUSEL = null; fecharPop();
    LINHAS = d.linhas.map(r => ({...r, qtd_calc: r.qtd, incluir: r.status === 'enviar' && r.tipo !== 'Teste'}));
    filtro = 'enviar'; busca = ''; $('busca').value = '';
    mensagem(''); $('resultado').hidden = false; desenhar();
    $('resultado').scrollIntoView({behavior:'smooth'});
  } catch (err) { mensagem(err.message, 'erro'); }
  atualizarBotao();
};

// ---------- tela de resultado ----------
const podeMarcar = r => r.status !== 'impedido';   // impedido pelo ML não envia; o resto pode ser marcado mesmo sem sugestão
const escopo = () => SKUSEL ? LINHAS.filter(r => SKUSEL.has(r.sku || '')) : LINHAS;   // linhas dos SKUs escolhidos (todas, se não há filtro)
function visiveis(){
  const q = busca.trim().toLowerCase();
  return LINHAS.filter(r => (filtro === 'todos' || r.status === filtro || (filtro === 'marcados' && r.incluir)) &&
    (!SKUSEL || SKUSEL.has(r.sku || '')) &&
    (!q || [r.sku, r.produto, r.codigo_ml, r.mlbu, ...(r.mlbs || [])].join(' ').toLowerCase().includes(q)));
}
function resumoSku(){
  const m = new Map();
  for (const r of escopo()) if (r.incluir && r.qtd > 0) { const t = m.get(r.sku) || {sku: r.sku || '(sem SKU)', qtd: 0}; t.qtd += r.qtd; m.set(r.sku, t); }
  return [...m.values()].sort((a, b) => a.sku.localeCompare(b.sku));
}
function desenhar(){
  const esc = escopo(), marc = esc.filter(r => r.incluir && r.qtd > 0), un = marc.reduce((s, r) => s + r.qtd, 0), skus = resumoSku();
  const geralUn = LINHAS.filter(r => r.incluir && r.qtd > 0).reduce((s, r) => s + r.qtd, 0), geral = SKUSEL ? ' (geral: ' + num(geralUn) + ')' : '';
  const cont = {}; esc.forEach(r => cont[r.status] = (cont[r.status] || 0) + 1);
  const testes = esc.filter(r => r.status === 'enviar' && r.tipo === 'Teste');
  const cards = $('cartoes'); cards.replaceChildren(
    el('div', {class:'card destaque'}, el('b', {}, num(marc.length)), el('span', {}, 'produtos marcados para enviar')),
    el('div', {class:'card destaque'}, el('b', {}, num(un)), el('span', {}, 'unidades no total' + (SKUSEL ? ' dos SKUs escolhidos' + geral : ''))),
    el('div', {class:'card'}, el('b', {}, num(skus.length)), el('span', {}, 'SKUs diferentes (produção)')),
    el('div', {class:'card'}, el('b', {}, num(cont.frete_alto || 0)), el('span', {}, 'barrados por frete alto')),
    el('div', {class:'card'}, el('b', {}, num(cont.sem_dados || 0)), el('span', {}, 'sem frete ou custo')),
    el('div', {class:'card'}, el('b', {}, num(cont.nao_precisa || 0)), el('span', {}, 'já têm estoque suficiente')));
  const fb = $('filtros'); fb.replaceChildren();
  const abas = [['enviar', 'Enviar'], ['marcados', 'Marcados'], ['frete_alto', 'Frete alto'], ['sem_dados', 'Sem frete/custo'], ['impedido', 'Impedidos'], ['nao_precisa', 'Não precisa'], ['todos', 'Todos']];
  for (const [k, nome] of abas) {
    const n = k === 'todos' ? esc.length : k === 'marcados' ? marc.length : (cont[k] || 0);
    fb.append(el('button', {class: k === filtro ? 'on' : '', onclick: () => { filtro = k; desenhar(); }}, nome + ' (' + n + ')'));
  }
  const pend = testes.filter(r => !r.incluir).length;
  $('barraTeste').hidden = !testes.length;
  $('avisoTeste').textContent = testes.length + ' produtos são envio-teste (sem estoque no Full ou sem vendas em 30 dias). Eles começam desmarcados: marque os que o Mercado Livre sugeriu ou digite a quantidade e aplique.' + (pend < testes.length ? ' ' + (testes.length - pend) + ' já marcados.' : '');
  const lista = visiveis(), corpo = $('corpo'); corpo.replaceChildren();
  for (const r of lista) corpo.append(linha(r));
  $('vazio').hidden = lista.length > 0;
  const cb = $('todos'); const marcaveis = lista.filter(podeMarcar); cb.checked = marcaveis.length > 0 && marcaveis.every(r => r.incluir); cb.disabled = !marcaveis.length;
  const cs = $('corpoSku'); cs.replaceChildren(...skus.map(t => el('tr', {}, el('td', {}, t.sku), el('td', {class:'n'}, num(t.qtd)))));
  $('totSku').textContent = num(un);
  $('baixarEnvio').disabled = $('baixarSku').disabled = marc.length === 0;
  rotuloSku(); barraSelecao(lista);
  $('notaSku').textContent = SKUSEL ? 'Filtro de SKUs ativo: as planilhas terão só os ' + SKUSEL.size + ' SKUs escolhidos.' : '';
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
function barraSelecao(lista){
  const n = lista.filter(r => r.incluir).length; $('barraSel').hidden = n === 0;
  $('txtSel').textContent = n + ' marcado' + (n === 1 ? '' : 's') + ' na tela';
}
$('selAplicar').onclick = () => {
  const v = Math.round(Number($('selQtd').value));
  if (!(v >= 1 && v <= 100000)) { mensagem('Digite a quantidade (de 1 a 100000) antes de aplicar.', 'erro'); return; }
  const a = visiveis().filter(r => r.incluir && podeMarcar(r));
  if (!a.length) { mensagem('Marque primeiro os produtos da tela.', 'erro'); return; }
  a.forEach(r => { r.qtd = v; }); desenhar(); mensagem(a.length + ' produtos com ' + v + ' unidade(s) cada.', 'ok');
};
$('busca').oninput = e => { busca = e.target.value; desenhar(); };
$('todos').onchange = e => { visiveis().filter(podeMarcar).forEach(r => { r.incluir = e.target.checked; }); desenhar(); };   // tudo que está na tela (filtros e SKUs), esteja ou não para enviar
const testes = () => escopo().filter(r => r.status === 'enviar' && r.tipo === 'Teste');
$('marcarTestes').onclick = () => { testes().forEach(r => r.incluir = true); desenhar(); };
$('marcarTestesUrg').onclick = () => { testes().forEach(r => { r.incluir = r.urgencia === 'Urgente'; }); desenhar(); };
$('limparTestes').onclick = () => { testes().forEach(r => r.incluir = false); desenhar(); };

$('aplicarQtdTeste').onclick = () => {
  const v = Math.round(Number($('qtdTesteTopo').value));
  if (!(v >= 1 && v <= 100000)) { mensagem('Digite a quantidade dos testes (de 1 a 100000) antes de aplicar.', 'erro'); return; }
  mensagem('');
  const alvo = visiveis().filter(r => r.status === 'enviar' && r.tipo === 'Teste');
  if (!alvo.length) { mensagem('Não há produtos de teste na tela. Mude o filtro ou os SKUs escolhidos.', 'erro'); return; }
  alvo.forEach(r => { r.qtd = v; r.incluir = true; });
  desenhar(); mensagem(alvo.length + ' testes marcados com ' + v + ' unidade(s) cada.', 'ok');
};

// ---------- filtro de SKUs (caixa de seleção com busca) ----------
let RASC = new Set();
const todosSkus = () => [...new Set(LINHAS.map(r => r.sku || ''))].sort((a, b) => (a || '~').localeCompare(b || '~'));
const nomeSku = s => s || SEMSKU;
function rotuloSku(){ $('btnSku').textContent = SKUSEL ? 'SKUs: ' + SKUSEL.size + ' de ' + todosSkus().length + ' ▾' : 'SKUs: todos ▾'; }
function listaPop(){
  const q = $('buscaSku').value.trim().toLowerCase(), lista = todosSkus().filter(s => !q || nomeSku(s).toLowerCase().includes(q));
  $('listaSku').replaceChildren(...lista.map(s => {
    const cb = el('input', {type:'checkbox', checked: RASC.has(s)});
    cb.onchange = () => { if (cb.checked) RASC.add(s); else RASC.delete(s); };
    return el('label', {}, cb, nomeSku(s));
  }));
  $('skuTodos').textContent = 'Selecionar tudo: ' + lista.length; $('skuCont').textContent = 'Mostrando ' + lista.length;
  $('skuTodos').onclick = () => { lista.forEach(s => RASC.add(s)); listaPop(); };
  $('skuLimpar').onclick = () => { lista.forEach(s => RASC.delete(s)); listaPop(); };
}
function fecharPop(){ $('popSku').hidden = true; $('btnSku').setAttribute('aria-expanded', 'false'); }
$('btnSku').onclick = e => {
  e.stopPropagation();
  if (!$('popSku').hidden) { fecharPop(); return; }
  RASC = new Set(SKUSEL || todosSkus()); $('buscaSku').value = ''; listaPop();
  $('popSku').hidden = false; $('btnSku').setAttribute('aria-expanded', 'true'); $('buscaSku').focus();
};
$('buscaSku').oninput = listaPop;
$('popSku').onclick = e => e.stopPropagation();
$('skuCancelar').onclick = fecharPop;
$('skuOk').onclick = () => { const todos = todosSkus(); SKUSEL = todos.every(s => RASC.has(s)) ? null : new Set([...RASC]); fecharPop(); desenhar(); };
document.addEventListener('click', fecharPop);
document.addEventListener('keydown', e => { if (e.key === 'Escape') fecharPop(); });

// ---------- downloads ----------
async function baixar(tipo){
  const botao = tipo === 'envio' ? $('baixarEnvio') : $('baixarSku'); botao.disabled = true;
  try {
    const linhas = escopo().map(r => ({incluir: r.incluir, qtd: r.qtd, qtd_calc: r.qtd_calc, status: r.status, tipo: r.tipo, codigo_ml: r.codigo_ml, mlbu: r.mlbu, sku: r.sku,
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

atualizarBotao();
