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
const CONFIRMACAO = 'APLICAR_PRECO_VENDAS';

let RAW = [], CAD = {}, LINHAS = [], SEL = new Set(), OVR = {}, INFO = {}, PEND = new Set(), IMPOSTOS = {}, RES = {};
let PERIODO = '', DIA = '', VERSAO = 0;
let MODO_ENVIAR = false, aba = 'mogilar', sub = 'a_enviar', classe = 'todas', busca = '';

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
const parametros = () => ({imposto_pct: $('vImposto').value, margem_padrao: $('vMargem').value, impostos: IMPOSTOS});
const chaveSku = r => r.sku || r.mlb;

// ---------- busca ----------
const DIAS_A_ENVIAR = 30;   // o UpSeller não tem filtro de data; o Mercado Livre só deixa ler até 31 dias por vez, então olhamos os últimos 30 dias de pedidos pagos (pedido a enviar mais antigo que isso quase não existe)
const dataIso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
// modo 'periodo': vendas feitas entre "De" e "Até". modo 'enviar': pedidos pagos que AINDA NÃO FORAM ENVIADOS (como a lista "Para enviar" do UpSeller), mesmo de dias anteriores
async function buscar(modo){
  const enviar = modo === 'enviar';
  MODO_ENVIAR = enviar;
  const escolhida = $(enviar ? 'vEConta' : 'vConta').value, contas = escolhida === 'AMBAS' ? ['ML19', 'ML44'] : [escolhida];
  let de = $('vDe').value, ate = $('vAte').value;
  if (enviar) { const hoje = new Date(), ini = new Date(); ini.setDate(ini.getDate() - DIAS_A_ENVIAR); de = dataIso(ini); ate = dataIso(hoje); }
  if (!de || !ate || ate < de) { mensagem('Confira as datas: "Até" não pode ser antes de "De".', 'erro'); return; }
  $('vBuscar').disabled = true; $('vEnviar').disabled = true; mensagem(''); $('vResultado').hidden = true;
  try {
    UPS = null; UPS_FILTRO = 'todos'; $('vUpsRes').textContent = '';
    RAW = []; SEL.clear(); OVR = {}; INFO = {}; PEND = new Set(); RES = {}; IMPOSTOS = {}; VERSAO++;
    const andamento = {}; for (const c of contas) andamento[c] = {off: 0, total: 0};
    const mostra = () => progresso(contas.map(c => c + ': ' + (andamento[c].total ? Math.min(andamento[c].off, andamento[c].total) + ' de ' + andamento[c].total + ' pedidos' : 'lendo…')).join(' · ') + (enviar ? ' (só os que faltam enviar)' : ''),
      5 + 55 * contas.reduce((t, c) => t + (andamento[c].total ? Math.min(1, andamento[c].off / andamento[c].total) : 0), 0) / contas.length);
    const pedir = (conta, off) => comTentativas(() => json('/api/vendas/pedidos', {conta, de, ate, offset: off, somente_a_enviar: enviar, etapa: enviar ? $('vEtapa').value : 'todos', prazo: enviar ? $('vPrazo').value : '', expira_horas: enviar && $('vEExpira').value !== '' ? Number($('vEExpira').value) : null}));
    await Promise.all(contas.map(async conta => {          // as duas contas são lidas ao mesmo tempo
      mostra();
      let d = await pedir(conta, 0);                        // 1ª página: descobre o total
      RAW.push(...d.itens); andamento[conta] = {off: d.proximo, total: d.total}; mostra();
      const posicoes = [];
      for (let o = d.proximo; !d.concluido && o < d.total; o += 20) posicoes.push(o);   // o resto das páginas, 4 de cada vez por conta
      let prox = 0;
      await Promise.all([0, 1, 2, 3].map(async () => {
        while (prox < posicoes.length) {
          const o = posicoes[prox++], r = await pedir(conta, o);
          RAW.push(...r.itens); andamento[conta].off += 20; mostra();
        }
      }));
    }));
    if (!RAW.length) { progresso(null); mensagem(enviar ? 'Nenhum pedido pago esperando envio nessa etapa.' : 'Nenhuma venda paga neste período.', ''); $('vBuscar').disabled = false; $('vEnviar').disabled = false; return; }
    const skus = [...new Set(RAW.map(r => r.sku).filter(Boolean))]; CAD = {};
    for (let i = 0; i < skus.length; i += 100) {
      progresso('Lendo custos e margens na planilha (' + Math.min(i + 100, skus.length) + ' de ' + skus.length + ' SKUs)…', 60 + 30 * i / skus.length);
      Object.assign(CAD, (await comTentativas(() => json('/api/vendas/cadastro', {skus: skus.slice(i, i + 100)}))).cadastro);
    }
    progresso('Lendo os impostos na planilha (TaxasML)…', 92);
    const contasVistas = [...new Set(RAW.map(r => r.conta))];
    const imp = await comTentativas(() => json('/api/vendas/impostos', {contas: contasVistas}));
    IMPOSTOS = imp.impostos;
    const falhas = Object.entries(imp.erros || {}).map(([c, e]) => c + ': ' + e);
    FILTRO = {de, ate, enviar, etapa: enviar ? $('vEtapa').value : 'todos', prazo: enviar ? $('vPrazo').value : '', expira_horas: enviar && $('vEExpira').value !== '' ? Number($('vEExpira').value) : null};
    PERIODO = de === ate ? de.split('-').reverse().join('/') : de.split('-').reverse().join('/') + ' a ' + ate.split('-').reverse().join('/'); DIA = ate;
    if (enviar) PERIODO = 'A enviar hoje (pedidos de ' + PERIODO + ')';
    progresso('Calculando o lucro…', 95);
    await recalcular();
    aba = 'mogilar'; sub = 'a_enviar'; classe = 'todas'; busca = ''; $('vBusca').value = ''; $('vExpira').value = '';
    if (!LINHAS.some(r => r.grupo !== 'full' && r.grupo === 'a_enviar')) sub = 'todos';
    $('vResultado').hidden = false; desenhar(); progresso(null);
    if (falhas.length && !$('vImposto').value) mensagem('Não consegui ler o imposto da planilha (' + falhas.join(' · ') + '). As vendas dessa conta ficam "Sem dados" até você digitar o imposto no campo.', 'erro');
    $('vResultado').scrollIntoView({behavior: 'smooth'});
  } catch (err) { progresso(null); mensagem(err.message, 'erro'); }
  $('vBuscar').disabled = false; $('vEnviar').disabled = false;
}
// cadastro da planilha + o que foi digitado na tela (custo e margem mínima valem para o SKU inteiro)
function cadastroEfetivo(){
  const c = JSON.parse(JSON.stringify(CAD));
  for (const [k, o] of Object.entries(OVR)) {
    const b = c[k] || {custo: null, margem: null, margem_full: null};
    if (o.custo !== undefined) b.custo = o.custo;
    if (o.meta !== undefined) { b.margem = o.meta; b.margem_full = o.meta; }
    c[k] = b;
  }
  return c;
}
async function recalcular(){
  const d = await json('/api/vendas/calcular', {linhas: RAW, cadastro: cadastroEfetivo(), parametros: parametros()});
  LINHAS = d.linhas.map((r, i) => Object.assign(r, {_i: i}));
}
let temporizador = null;
function recalcularDepois(){
  if (!RAW.length) return;
  clearTimeout(temporizador);
  temporizador = setTimeout(async () => { try { mensagem(''); await recalcular(); desenhar(); } catch (e) { mensagem(e.message, 'erro'); } }, 500);
}
for (const id of ['vImposto', 'vMargem']) $(id).oninput = recalcularDepois;

// ---------- escopo da tela ----------
const daAba = r => aba === 'full' ? r.grupo === 'full' : r.grupo !== 'full';
const noSub = r => aba === 'full' || sub === 'todos' || r.grupo === sub;
const podeMarcar = r => r.grupo === 'a_enviar';
const chaveInfo = r => [r.conta, r.mlb, r.custo_unit, r.meta, r.imposto_pct].join('|');
const precisaPreco = r => (r.classe === 'abaixo' || r.classe === 'prejuizo') && r.custo_unit && r.imposto_pct !== null && r.imposto_pct !== undefined;
const RANK = {sem_dados: 0, prejuizo: 1, abaixo: 2, dentro: 3};
// sem dados (para a pessoa completar custo/margem; agrupados por SKU) → prejuízo (maior primeiro) → abaixo da margem (maior perda primeiro) → dentro da margem (do menor ao maior lucro)
function ordem(a, b){
  if (RANK[a.classe] !== RANK[b.classe]) return RANK[a.classe] - RANK[b.classe];
  if (a.classe === 'prejuizo' || a.classe === 'abaixo') return (b.perda || 0) - (a.perda || 0);
  if (a.classe === 'dentro') return a.lucro - b.lucro;
  return String(a.sku || a.mlb).localeCompare(String(b.sku || b.mlb)) || String(a.pedido).localeCompare(String(b.pedido));
}
const naUps = r => !!UPS && (UPS.has(String(r.pedido)) || (!!r.pack_id && UPS.has(String(r.pack_id))) || (!!r.pedido_upseller && UPS.has(String(r.pedido_upseller))));   // o número do UpSeller pode ser o do pedido ou o do carrinho
const soUps = r => !!r.pedido_upseller;      // linha trazida só porque está no UpSeller (a busca não tinha trazido)
// filtro "expira em X horas" (como o Modo Rápido do UpSeller): prazo de despacho até agora + X h; vencidos entram; sem prazo conhecido continua na lista
const expiraEm = r => { const h = $('vExpira').value; if (h === '' || !r.limite_dt) return true; const t = Date.parse(r.limite_dt); return Number.isNaN(t) || t <= Date.now() + Number(h) * 3600000; };
function visiveis(){
  const q = busca.trim().toLowerCase(), f = UPS ? UPS_FILTRO : 'todos';
  return LINHAS.filter(r => (f === 'so_ups' ? soUps(r) : daAba(r) && noSub(r)) && (classe === 'todas' || (classe === 'ruins' ? (r.classe === 'abaixo' || r.classe === 'prejuizo') : r.classe === classe)) && expiraEm(r)
    && (f === 'todos' || (f === 'ambos' ? naUps(r) && !soUps(r) : f === 'so_site' ? !naUps(r) : true))
    && (!q || [r.pedido, r.pack_id, r.envio_id, r.pedido_upseller, r.sku, r.titulo, r.mlb].join(' ').toLowerCase().includes(q))).sort(ordem);
}

const infoDe = r => INFO[chaveInfo(r)];
const selecionavel = r => { const i = infoDe(r); return !!(i && i.acao === 'subir'); };

// ---------- tela ----------
function copiarTexto(t){
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(t);
  return new Promise((ok, falha) => { const a = el('textarea', {value: t}); document.body.append(a); a.select(); try { document.execCommand('copy') ? ok() : falha(new Error('copiar')); } catch (e) { falha(e); } a.remove(); });
}
function botaoCopiar(texto){
  const b = el('button', {class:'copiar', type:'button', title:'Copiar o número do pedido', 'aria-label':'Copiar o número do pedido'}, '⧉');
  b.onclick = async e => { e.stopPropagation(); try { await copiarTexto(String(texto)); b.textContent = '✓'; } catch (err) { b.textContent = '!'; } setTimeout(() => { b.textContent = '⧉'; }, 1200); };
  return b;
}
// conferência com o UpSeller: a pessoa cola os números dos pedidos de lá; a tela marca os daqui que não estão lá
let UPS = null, UPS_FILTRO = 'todos';   // UPS_FILTRO: todos | ambos (UpSeller e site) | so_site (site, não UpSeller) | so_ups (UpSeller, não veio no site)
const numerosDe = t => new Set((String(t || '').match(/\d{12,}/g) || []));
const chavesSite = () => { const c = new Set(); for (const r of LINHAS) if (!soUps(r)) { c.add(String(r.pedido)); if (r.pack_id) c.add(String(r.pack_id)); } return c; };
function conferirUpseller(manter){
  const nums = numerosDe($('vUpsTexto').value), r = $('vUpsRes');
  if (!nums.size) { UPS = null; UPS_FILTRO = 'todos'; r.textContent = 'Cole os números dos pedidos do UpSeller (com 12 dígitos ou mais).'; desenhar(); return; }
  UPS = nums; if (!manter) UPS_FILTRO = 'todos';
  const site = chavesSite(), foraSet = new Set(), ambosSet = new Set();
  for (const x of LINHAS) if (!soUps(x)) (naUps(x) ? ambosSet : foraSet).add(String(x.pedido));
  const fora = [...foraSet], semAqui = [...nums].filter(n => !site.has(n));
  const cancUps = semAqui.filter(n => UPS_CANC.has(n)), semAquiVivos = semAqui.filter(n => !UPS_CANC.has(n));
  const noUpsSite = nums.size - semAqui.length, pl = PLANILHA && PLANILHA.ml === nums.size ? PLANILHA : null;
  const linhasTxt = pl ? 'A planilha tem ' + pl.linhas + ' linhas: ' + pl.ml + ' pedidos do Mercado Livre' + (pl.outras ? ' + ' + pl.outras + ' de outras lojas (ex.: Shopee, não entram na conta)' : '') + (pl.repetidos ? ' + ' + pl.repetidos + ' repetidos' : '') + '. ' : '';
  r.replaceChildren(document.createTextNode(linhasTxt + 'Dos ' + nums.size + ' pedidos do UpSeller: ' + noUpsSite + ' estão no site e ' + semAqui.length + ' não vieram na busca' + (cancUps.length ? ' (' + cancUps.length + ' deles o próprio UpSeller marca como Cancelado, então não precisam vir)' : '') + (semAquiVivos.length ? ': ' + semAquiVivos.slice(0, 30).join(', ') + (semAquiVivos.length > 30 ? '…' : '') : '') + '. O site trouxe ' + (foraSet.size + ambosSet.size) + ' pedidos, e ' + foraSet.size + ' deles não estão no UpSeller. '));
  const opc = [['todos', 'Todos'], ['ambos', 'Só os que estão no UpSeller e no site (' + noUpsSite + ')'], ['so_site', 'Só os que estão no site e não no UpSeller (' + fora.length + ')'], ['so_ups', 'Só os que estão no UpSeller e não apareceram no site (' + (semAquiVivos.length + cancUps.filter(n => UPS_MAE[n]).length) + ')']];
  const grupo = el('div', {class:'filtros'}); for (const [k, nome] of opc) grupo.append(el('button', {type:'button', class: UPS_FILTRO === k ? 'on' : '', onclick: () => { UPS_FILTRO = k; conferirUpseller(true); }}, nome));
  r.append(grupo);
  desenhar();
}
// anexar a planilha do UpSeller: lê os números e, para os que NÃO vieram aqui, pergunta ao Mercado Livre o motivo
let UPS_MAE = {}, UPS_CANC = new Set(), PLANILHA = null, UPS_CONTA = {}, FILTRO = {de: '', ate: '', enviar: false, etapa: 'todos', prazo: ''};
async function anexarUpseller(arquivo){
  const r = $('vUpsRes'); r.textContent = 'Lendo a planilha…'; $('vUpsMotivosCx').hidden = true;
  try {
    const fd = new FormData(); fd.append('planilha', arquivo);
    const resp = await fetch('/api/vendas/upseller-ler', {method:'POST', body: fd});
    if (resp.status === 401) { location.href = '/login'; return; }
    let d = null; try { d = await resp.json(); } catch(e) {}
    if (!resp.ok || !d || d.ok === false) throw new Error((d && d.error) || ('Erro ' + resp.status));
    UPS_CONTA = {}; for (const p of d.pedidos) UPS_CONTA[p.pedido] = p.conta;
    UPS_CANC = new Set(d.pedidos.filter(p => p.cancelado).map(p => p.pedido));
    UPS_MAE = {}; for (const p of d.pedidos) if (p.mae) UPS_MAE[p.pedido] = Object.assign({conta: p.conta}, p.mae);
    PLANILHA = {linhas: d.linhas, ml: d.pedidos.length, outras: d.outras_lojas, repetidos: d.repetidos};
    $('vUpsTexto').value = d.pedidos.map(p => p.pedido).join('\n');
    conferirUpseller();
    await explicarFaltantes();
  } catch (e) { r.textContent = 'Não consegui ler a planilha: ' + e.message; }
}
async function explicarFaltantes(){
  const site = chavesSite(), faltam = [...(UPS || [])].filter(n => !site.has(n)), cx = $('vUpsMotivosCx'), corpo = $('vUpsMotivos');
  corpo.replaceChildren(); cx.hidden = !faltam.length; if (!faltam.length) return;
  const canc = faltam.filter(n => UPS_CANC.has(n)), vivos = faltam.filter(n => !UPS_CANC.has(n));
  const limite = vivos.slice(0, 200), res = $('vUpsMotivoTxt'), base = '';
  const linhas = [];
  for (let i = 0; i < limite.length; i += 8) {
    res.textContent = 'Descobrindo o motivo… ' + Math.min(i + 8, limite.length) + ' de ' + limite.length;
    try {
      const d = await comTentativas(() => json('/api/vendas/diagnostico-pedidos', Object.assign({}, FILTRO, {somente_a_enviar: FILTRO.enviar, pedidos: limite.slice(i, i + 8).map(n => ({pedido: n, conta: UPS_CONTA[n] || ''}))})));
      for (const x of d.resultados) linhas.push(x);
    } catch (e) { for (const n of limite.slice(i, i + 8)) linhas.push({pedido: n, conta: UPS_CONTA[n] || '', situacao: '', motivo: 'Não consegui consultar agora (' + e.message + ')'}); }
    corpo.replaceChildren(...linhas.map(x => el('tr', {}, el('td', {}, el('span', {class:'pedido-n'}, x.pedido, botaoCopiar(x.pedido))), el('td', {}, x.conta || '—'), el('td', {}, x.situacao || '—'), el('td', {}, x.motivo || 'Deveria ter aparecido: busque de novo (pode ter sido só uma falha de leitura do Mercado Livre).'))));
  }
  const grupos = new Map(); for (const x of linhas) { const k = x.motivo ? x.motivo.replace(/"[^"]*"/g, '“…”').replace(/\d{2}\/\d{2}/g, 'dd/mm') : 'Deveria ter aparecido'; grupos.set(k, (grupos.get(k) || 0) + 1); }
  for (const n of canc) linhas.push({pedido: n, conta: UPS_CONTA[n] || '', situacao: 'Cancelado no UpSeller', motivo: UPS_MAE[n] ? 'Venda separada em pacotes: entrou na lista como venda mãe, com o valor total da venda (valores do UpSeller)' : 'O próprio UpSeller marca este pedido como Cancelado (o Mercado Livre cancelou ou separou a venda em pacotes novos). Não precisa vir na lista'});
  corpo.replaceChildren(...linhas.map(x => el('tr', {}, el('td', {}, el('span', {class:'pedido-n'}, x.pedido, botaoCopiar(x.pedido))), el('td', {}, x.conta || '—'), el('td', {}, x.situacao || '—'), el('td', {}, x.motivo))));
  await trazerFaltantes(limite, canc.filter(n => UPS_MAE[n]));
  res.textContent = 'Motivos dos ' + linhas.length + ' que faltam' + (canc.length ? ' (' + canc.length + ' cancelados no UpSeller)' : '') + ': ' + [...grupos].sort((a, b) => b[1] - a[1]).map(([k, v]) => v + ' × ' + k).join(' | ') + (vivos.length > limite.length ? ' (mostrei só os primeiros ' + limite.length + ')' : '');
}
$('vUpsArquivo').onchange = e => { const f = e.target.files[0]; if (f) anexarUpseller(f); e.target.value = ''; };
// coloca na lista (com lucro e margem) os pedidos que estão no UpSeller e a busca não trouxe
function linhasDaMae(n){
  const m = UPS_MAE[n], soma = m.itens.reduce((t, i) => t + i.preco * i.quantidade, 0), arred = v => v === null || v === undefined ? null : Math.round(v * 100) / 100;
  return m.itens.map(i => { const venda = arred(i.preco * i.quantidade), parte = soma > 0 ? venda / soma : 1 / m.itens.length;
    return {conta: m.conta, pedido: n, pack_id: n, pedido_upseller: n, data: m.data, mlb: i.mlb, sku: i.sku, titulo: i.titulo, quantidade: i.quantidade, venda,
      tarifa: arred(m.tarifa === null ? null : m.tarifa * parte), frete: arred(m.frete === null ? null : m.frete * parte), grupo: 'a_enviar', logistica: '', status_envio: '', substatus: '',
      status_rotulo: 'Venda separada (valor da venda mãe)', obs: 'Venda separada em pacotes pelo Mercado Livre: valor total, tarifa e frete da venda mãe, como no UpSeller', preco_ml: null}; });
}
async function trazerFaltantes(numeros, maes = []){
  RAW = RAW.filter(r => !r.pedido_upseller);
  for (const n of maes) RAW.push(...linhasDaMae(n));
  const lotes = []; for (let i = 0; i < numeros.length; i += 8) lotes.push(numeros.slice(i, i + 8));
  let prox = 0, ignorados = 0;
  const msg = $('vUpsMotivoTxt');
  await Promise.all([0, 1, 2].map(async () => {
    while (prox < lotes.length) {
      const lote = lotes[prox++];
      try {
        const d = await comTentativas(() => json('/api/vendas/pedidos-por-numero', {pedidos: lote.map(n => ({pedido: n, conta: UPS_CONTA[n] || ''}))}));
        RAW.push(...d.itens); ignorados += d.ignorados.length;
        msg.textContent = 'Trazendo para a lista os pedidos do UpSeller que faltavam… ' + RAW.filter(r => r.pedido_upseller).length + ' linhas';
      } catch (e) { ignorados += lote.length; }
    }
  }));
  const novos = RAW.filter(r => r.pedido_upseller);
  if (!novos.length) return 0;
  const faltaSku = [...new Set(novos.map(r => r.sku).filter(k => k && !CAD[k]))];
  for (let i = 0; i < faltaSku.length; i += 100) Object.assign(CAD, (await comTentativas(() => json('/api/vendas/cadastro', {skus: faltaSku.slice(i, i + 100)}))).cadastro);
  const contasNovas = [...new Set(novos.map(r => r.conta))].filter(c => !IMPOSTOS[c]);
  if (contasNovas.length) { try { Object.assign(IMPOSTOS, (await json('/api/vendas/impostos', {contas: contasNovas})).impostos); } catch (e) {} }
  await recalcular(); conferirUpseller(true);
  return novos.length;
}
function resumoEnvio(){
  const p = $('vResumoEnvio'); p.hidden = !MODO_ENVIAR || !LINHAS.length; if (p.hidden) return;
  const por = new Map(), pedidos = new Set();
  for (const r of LINHAS) { const k = (r.status_rotulo || '—').replace(/ · enviar até.*$/, ''), id = r.conta + '|' + r.pedido; pedidos.add(id); if (!por.has(k)) por.set(k, new Set()); por.get(k).add(id); }
  const faltas = new Map();
  for (const r of LINHAS) if (r.classe === 'sem_dados') for (const f of String(r.pendencias || 'motivo não informado').split(',')) { const k = f.trim() || 'motivo não informado'; faltas.set(k, (faltas.get(k) || 0) + 1); }
  const semDados = faltas.size ? ' Sem dados para calcular (linhas): ' + [...faltas].sort((a, b) => b[1] - a[1]).map(([k, v]) => k + ' ' + v).join(' · ') + '.' : '';
  p.textContent = pedidos.size + ' pedido(s) esperando envio, por situação no Mercado Livre: ' + [...por].sort((a, b) => b[1].size - a[1].size).map(([k, v]) => k + ' ' + v.size).join(' · ') + '. (Um pedido com vários produtos aparece em várias linhas abaixo.)' + semDados;
}
function desenhar(){
  resumoEnvio();
  const esc = LINHAS.filter(r => daAba(r) && noSub(r)), c = {prejuizo: 0, abaixo: 0, dentro: 0, sem_dados: 0}; let prej = 0, deixou = 0;
  esc.forEach(r => { c[r.classe]++; if (r.perda_tipo === 'prejuizo') prej += r.perda; else if (r.perda_tipo === 'deixou') deixou += r.perda; });
  const marcados = LINHAS.filter(r => SEL.has(r._i) && podeMarcar(r));        // as marcadas que ainda serão enviadas por você = lista "Não enviar"
  const cartoes = [
    el('div', {class:'card'}, el('b', {}, num(esc.length)), el('span', {}, 'itens vendidos (nesta lista)')),
    el('div', {class:'card destaque'}, el('b', {}, num(c.dentro)), el('span', {}, 'dentro da margem')),
    el('div', {class:'card'}, el('b', {}, num(c.abaixo)), el('span', {}, 'abaixo da margem · deixou de ganhar ' + brl(deixou))),
    el('div', {class:'card risco'}, el('b', {}, num(c.prejuizo)), el('span', {}, 'com prejuízo · perdeu ' + brl(prej))),
    el('div', {class:'card'}, el('b', {}, num(c.sem_dados)), el('span', {}, 'sem dados (digite o custo ou a margem na linha)'))];
  if (SEL.size) cartoes.push(el('div', {class:'card risco'}, el('b', {}, num(SEL.size)), el('span', {}, 'vendas marcadas')));
  $('vCartoes').replaceChildren(...cartoes);
  const ab = $('vAbas'); ab.replaceChildren();
  for (const [k, nome, n] of [['mogilar', 'Vendas Mogilar (não Full)', LINHAS.filter(r => r.grupo !== 'full').length], ['full', 'Vendas Full', LINHAS.filter(r => r.grupo === 'full').length]])
    ab.append(el('button', {class: k === aba ? 'on' : '', onclick: () => { aba = k; classe = 'todas'; desenhar(); }}, nome + ' (' + n + ')'));
  const gb = $('vGrupos'); gb.replaceChildren(); gb.hidden = aba === 'full';
  if (aba !== 'full') for (const [k, nome] of [['a_enviar', 'A enviar'], ['enviado', 'Já enviados'], ['todos', 'Todas']])
    gb.append(el('button', {class: k === sub ? 'on' : '', onclick: () => { sub = k; desenhar(); }}, nome + ' (' + (k === 'todos' ? LINHAS.filter(r => r.grupo !== 'full').length : LINHAS.filter(r => r.grupo === k).length) + ')'));
  const cb = $('vClasses'); cb.replaceChildren();
  for (const [k, nome] of [['todas', 'Todas'], ['ruins', 'Abaixo da margem + prejuízo'], ['prejuizo', 'Prejuízo'], ['abaixo', 'Abaixo da margem'], ['dentro', 'Dentro da margem'], ['sem_dados', 'Sem dados']])
    cb.append(el('button', {class: k === classe ? 'on' : '', onclick: () => { classe = k; desenhar(); }}, nome + ' (' + (k === 'todas' ? esc.length : k === 'ruins' ? (c.abaixo || 0) + (c.prejuizo || 0) : (c[k] || 0)) + ')'));
  cabecalho();
  const lista = visiveis(), corpo = $('vCorpo'); corpo.replaceChildren(...lista.map(linha));
  $('vVazio').hidden = lista.length > 0;
  const nEx = SEL.size; $('vBaixarSel').disabled = !nEx; $('vBaixarSel').textContent = 'Baixar Excel das vendas marcadas (' + nEx + ')';
  const te = $('vTodosSel'); if (te) { te.checked = lista.length > 0 && lista.every(r => SEL.has(r._i)); te.disabled = !lista.length; }
  $('vBaixarNao').disabled = !marcados.length; $('vBaixarNao').textContent = 'Baixar lista “Não enviar” (' + marcados.length + ')';
  $('vMarcarRuins').disabled = !lista.some(r => r.classe === 'abaixo' || r.classe === 'prejuizo'); $('vLimpar').disabled = !SEL.size;
  const sel = LINHAS.filter(r => SEL.has(r._i) && selecionavel(r));
  { const n = new Set(LINHAS.filter(r => SEL.has(r._i)).map(r => r.conta + '|' + r.mlb)).size; $('vSku').disabled = !n; $('vPausar').disabled = !n; $('vSku').textContent = 'Trocar SKU dos marcados (' + n + ')'; $('vPausar').textContent = 'Pausar anúncios marcados (' + n + ')'; }
  $('vPreco').disabled = !sel.length; $('vPreco').textContent = 'Atualizar preço no Mercado Livre (' + sel.length + ')';
  { // explica por que marcadas não entram no preço (nada some em silêncio)
    const m = LINHAS.filter(r => SEL.has(r._i)); let ja = 0, calc = 0, bloq = 0, sem = 0;
    for (const r of m) { if (selecionavel(r)) continue; const i = infoDe(r); if (!precisaPreco(r)) sem++; else if (!i) calc++; else if (i.acao === 'ok') ja++; else bloq++; }
    const partes = [];
    if (ja) partes.push(ja + ' já atingem a margem com o preço de hoje no Mercado Livre');
    if (calc) partes.push(calc + ' ainda calculando o preço novo');
    if (bloq) partes.push(bloq + ' bloqueadas (passe o mouse na coluna do preço novo para ver o motivo)');
    if (sem) partes.push(sem + ' sem custo/imposto ou dentro da margem');
    $('vPrecoDica').textContent = m.length && !sel.length ? 'Nenhuma marcada pode ter o preço atualizado: ' + partes.join('; ') + '.' : (partes.length ? 'Fora da atualização de preço: ' + partes.join('; ') + '.' : '');
  }
  const negs = LINHAS.filter(r => r.classe === 'prejuizo' || r.classe === 'abaixo').length; $('vBaixarNeg').disabled = !negs; $('vBaixarNeg').textContent = 'Baixar Excel dos prejuízos e abaixo da margem (' + negs + ')';
  resumoManual();
  buscarPrecos();
}
function cabecalho(){
  const t = (txt, cls, dica) => el('th', {class: cls || '', title: dica || ''}, txt);
  const cols = [];
  cols.push(el('th', {class:'chk', title:'Marcar as vendas: vale para baixar em Excel, atualizar o preço (só as que podem) e a lista “Não enviar” (só as que ainda serão enviadas)'}, Object.assign(el('input', {type:'checkbox', id:'vTodosSel', title:'Marcar/desmarcar tudo que está na tela'}), {onchange: e => { visiveis().forEach(r => { if (e.target.checked) SEL.add(r._i); else SEL.delete(r._i); }); desenhar(); }})), t('Situação'), t('UpSeller', '', 'OK = esta venda está na lista que você colou do UpSeller'), t('Prejuízo / deixou de ganhar', 'n', 'Prejuízo (vermelho) ou quanto deixou de ganhar para chegar na margem mínima'), t('Conta'), t('Pedido'), t('Nº do envio', '', 'Número do envio (o #4841… do Mercado Livre) e número da venda no Mercado Livre. Qualquer um deles funciona na busca.'), t('SKU'), t('Status do envio'), t('MLB'), t('Produto'),
    t('Qtd', 'n'), t('Venda', 'n'), t('Tarifa ML', 'n'), t('Frete', 'n'), t('Imposto', 'n'), t('Custo unit.', 'n', 'Digite para corrigir ou preencher (vale para o SKU todo, só nesta tela)'), t('Lucro', 'n'), t('Margem', 'n'),
    t('Margem mínima', 'n', 'Digite para corrigir ou preencher (vale para o SKU todo, só nesta tela)'), t('Preço no ML', 'n'), t('Preço necessário', 'n', 'Preço do anúncio para atingir a margem mínima, com tarifa e frete reais'),
    t('Envio'));
  $('vCab').replaceChildren(...cols);
}
function campoEditavel(r, campo){
  const k = chaveSku(r), editado = OVR[k] && OVR[k][campo] !== undefined;
  const atual = campo === 'custo' ? r.custo_unit : r.meta;
  const i = el('input', {type:'number', min:0, step:'0.01', value: atual === null || atual === undefined ? '' : atual, placeholder: '—', class: editado ? 'editado' : '', title: editado ? 'Valor digitado por você (a planilha não foi alterada)' : (campo === 'meta' && r.meta_origem === 'padrão' ? 'Margem padrão: digite a margem deste produto' : 'Valor da planilha')});
  i.onchange = () => {
    const txt = String(i.value).replace(',', '.').trim(), v = Number(txt);
    OVR[k] = OVR[k] || {};
    if (txt === '') delete OVR[k][campo]; else if (Number.isFinite(v) && v >= 0 && !(campo === 'custo' && v === 0)) OVR[k][campo] = v; else { mensagem('Digite um número válido.', 'erro'); desenhar(); return; }
    if (!Object.keys(OVR[k]).length) delete OVR[k];
    mensagem(''); recalcular().then(() => { INFO = {}; desenhar(); }).catch(e => mensagem(e.message, 'erro'));
  };
  return i;
}
function linha(r){
  const cels = [];
  const sinal = v => v === null || v === undefined ? '' : (v < 0 ? 'n neg' : 'n pos');
  const info = infoDe(r);
  let precoAtual = r.preco_ml === null || r.preco_ml === undefined ? '—' : brl(r.preco_ml), precoNovo = '—';
  if (precisaPreco(r)) {
    if (info) {
      precoAtual = info.preco_atual !== undefined && info.preco_atual !== null ? brl(info.preco_atual) : '—';
      if (info.acao === 'subir') precoNovo = el('span', {class:'neg-claro', title: info.mensagem}, brl(info.preco_novo), el('div', {class:'mlbs'}, '+' + pct(info.variacao_pct) + ' → margem ' + pct(info.margem_nova)));
      else precoNovo = el('span', {class:'mlbs', title: info.mensagem}, info.acao === 'ok' ? 'já atinge a margem' : 'bloqueado: ' + info.mensagem);
    } else precoNovo = el('span', {class:'mlbs'}, PEND.has(chaveInfo(r)) ? 'calculando…' : '…');
  } else if (r.classe === 'sem_dados') precoNovo = el('span', {class:'mlbs'}, /custo|SKU/.test(r.pendencias || '') ? 'digite o custo' : 'falta: ' + (r.pendencias || 'dado'));
  const cb = el('input', {type:'checkbox', checked: SEL.has(r._i), title: 'Marcar esta venda (Excel, atualizar preço' + (selecionavel(r) ? ' — este anúncio pode ter o preço atualizado' : '') + (podeMarcar(r) ? ', lista “Não enviar”' : '') + ')'});
  cb.onchange = () => { if (cb.checked) SEL.add(r._i); else SEL.delete(r._i); desenhar(); };
  const ups = !UPS ? el('span', {class:'mlbs', title:'Cole a lista do UpSeller na caixa acima para conferir'}, '—') : (naUps(r) ? el('span', {class:'tag v-dentro'}, 'OK') : el('span', {class:'tag v-abaixo', title:'Este pedido não está na lista que você colou do UpSeller'}, 'não achei'));
  cels.push(
    el('td', {class:'chk'}, cb),
    el('td', {}, el('span', {class:'tag v-' + r.classe, title: r.pendencias ? 'Falta: ' + r.pendencias : (r.meta_origem === 'padrão' ? 'Usou a margem mínima padrão (produto sem margem na planilha)' : '')}, ROT[r.classe])),
    el('td', {}, ups),
    el('td', {class: 'n ' + (r.perda_tipo === 'prejuizo' ? 'neg' : '')}, r.perda ? el('span', {}, brl(r.perda), el('div', {class:'mlbs'}, r.perda_tipo === 'prejuizo' ? 'prejuízo' : 'deixou de ganhar')) : '—'),
    el('td', {}, r.conta), el('td', {}, el('span', {class:'pedido-n'}, r.pedido, botaoCopiar(r.pedido)), el('div', {class:'mlbs'}, r.data), soUps(r) ? el('div', {}, el('span', {class:'tag v-abaixo', title:'Este pedido está no UpSeller e a busca não o tinha trazido; foi buscado no Mercado Livre pelo número'}, 'só no UpSeller')) : ''),
    el('td', {}, r.envio_id ? el('span', {class:'pedido-n', title:'Número do envio'}, r.envio_id, botaoCopiar(r.envio_id)) : '—', r.pack_id && r.pack_id !== r.pedido ? el('div', {class:'mlbs', title:'Número da venda no Mercado Livre (carrinho)'}, 'venda ML ' + r.pack_id, botaoCopiar(r.pack_id)) : ''), el('td', {}, r.sku || '—'),
    el('td', {}, el('span', {class:'tag e-' + r.grupo, title: r.status_envio ? r.status_envio + (r.substatus ? ' / ' + r.substatus : '') : ''}, r.status_rotulo || '—')),
    el('td', {}, r.mlb),
    el('td', {}, r.titulo.slice(0, 70), r.obs ? el('div', {class:'mlbs'}, r.obs) : ''),
    el('td', {class:'n'}, num(r.quantidade)), el('td', {class:'n'}, brl(r.venda)), el('td', {class:'n'}, brl(r.tarifa)), el('td', {class:'n'}, brl(r.frete)), el('td', {class:'n'}, brl(r.imposto)),
    el('td', {class:'n'}, campoEditavel(r, 'custo')), el('td', {class: sinal(r.lucro)}, brl(r.lucro)), el('td', {class: sinal(r.margem)}, pct(r.margem)),
    el('td', {class:'n'}, campoEditavel(r, 'meta')), el('td', {class:'n'}, precoAtual), el('td', {class:'n'}, precoNovo), el('td', {}, ENV[r.grupo] || r.grupo));
  return el('tr', {class: SEL.has(r._i) ? 'marcada' : ''}, ...cels);
}
$('vUpsConferir').onclick = async () => { UPS_CONTA = {}; PLANILHA = null; UPS_CANC = new Set(); UPS_MAE = {}; conferirUpseller(); await explicarFaltantes(); };
$('vUpsLimpar').onclick = () => { $('vUpsMotivosCx').hidden = true; $('vUpsMotivoTxt').textContent = ''; $('vUpsTexto').value = ''; UPS = null; UPS_FILTRO = 'todos'; $('vUpsRes').textContent = ''; desenhar(); };
$('vBusca').oninput = e => { busca = e.target.value; desenhar(); };
$('vExpira').onchange = () => desenhar();
$('vMarcarRuins').onclick = () => {
  const a = visiveis().filter(r => r.classe === 'abaixo' || r.classe === 'prejuizo'); a.forEach(r => SEL.add(r._i)); desenhar();
  mensagem(a.length + ' vendas marcadas para não enviar. Desmarque na lista as que quiser enviar mesmo assim.', 'ok');
};
$('vLimpar').onclick = () => { SEL.clear(); desenhar(); mensagem(''); };

// ---------- preço necessário (somente leitura, calculado aos poucos para as vendas da lista aberta) ----------
let calculando = false;
async function buscarPrecos(){
  if (calculando) return;
  const versao = VERSAO, imposto = parametros();
  const falta = new Map();
  for (const r of [...LINHAS].sort((x, y) => (daAba(y) ? 1 : 0) - (daAba(x) ? 1 : 0))) if (precisaPreco(r)) { const k = chaveInfo(r); if (!INFO[k] && !falta.has(k)) falta.set(k, r); }
  if (!falta.size) return;
  calculando = true;
  try {
    const porConta = {};
    for (const [k, r] of falta) { PEND.add(k); (porConta[r.conta] = porConta[r.conta] || []).push({k, mlb: r.mlb, custo: r.custo_unit, meta: r.meta, imposto_pct: r.imposto_pct}); }
    for (const conta of Object.keys(porConta)) {
      const lista = porConta[conta];
      for (let i = 0; i < lista.length; i += 6) {
        if (versao !== VERSAO) return;
        const lote = lista.slice(i, i + 6);
        try {
          const d = await comTentativas(() => json('/api/vendas/preco-alvo', {conta, parametros: imposto, itens: lote.map(x => ({mlb: x.mlb, custo: x.custo, meta: x.meta, imposto_pct: x.imposto_pct}))}), 2);
          if (versao !== VERSAO) return;
          for (const x of lote) { INFO[x.k] = d.resultados[x.mlb] || {acao: 'bloqueado', mensagem: 'sem resposta'}; PEND.delete(x.k); }
        } catch (e) {
          for (const x of lote) { INFO[x.k] = {acao: 'bloqueado', mensagem: e.message}; PEND.delete(x.k); }
        }
        redesenharLinhas();
      }
    }
  } finally { calculando = false; }
  if (versao === VERSAO) { const ainda = LINHAS.some(r => precisaPreco(r) && !INFO[chaveInfo(r)]); if (ainda) buscarPrecos(); }
}
function redesenharLinhas(){ if (!$('vResultado').hidden) desenhar(); }

// ---------- atualizar preço no Mercado Livre ----------
let ALVOS = [];
function alvosSelecionados(){
  const por = new Map();
  for (const r of LINHAS) {
    if (!SEL.has(r._i) || !selecionavel(r)) continue;
    const k = r.conta + '|' + r.mlb, i = infoDe(r), ant = por.get(k);
    if (!ant || r.meta > ant.meta) por.set(k, {conta: r.conta, mlb: r.mlb, sku: r.sku, custo: r.custo_unit, meta: r.meta, imposto_pct: r.imposto_pct, info: i, pedidos: ant ? ant.pedidos : []});
    por.get(k).pedidos.push(String(r.pedido));
  }
  return [...por.values()];
}
$('vPreco').onclick = () => {
  ALVOS = alvosSelecionados(); if (!ALVOS.length) return;
  $('vPrecoLista').replaceChildren(...ALVOS.map(a => el('tr', {}, el('td', {}, a.conta), el('td', {}, a.mlb), el('td', {}, a.sku || '—'), el('td', {class:'n'}, brl(a.info.preco_atual)), el('td', {class:'n neg-claro'}, brl(a.info.preco_novo)),
    el('td', {class:'n'}, '+' + pct(a.info.variacao_pct)), el('td', {class:'n'}, pct(a.info.margem_nova)))));
  $('vLiberarCx').hidden = !ALVOS.some(a => a.info.requer_liberacao); $('vLiberar').checked = false; $('vEntendi').checked = false; $('vPrecoOk').disabled = true;
  $('vPrecoRes').hidden = true; $('vPainelPreco').hidden = false; $('vPainelPreco').scrollIntoView({behavior:'smooth', block:'center'});
};
$('vEntendi').onchange = () => { $('vPrecoOk').disabled = !$('vEntendi').checked; };
$('vPrecoCancela').onclick = () => { $('vPainelPreco').hidden = true; };
$('vPrecoOk').onclick = async () => {
  const b = $('vPrecoOk'); b.disabled = true; $('vPrecoCancela').disabled = true;
  const res = $('vPrecoRes'); res.hidden = false; res.className = 'aviso'; res.textContent = 'Atualizando…';
  const linhasRes = []; let ok = 0;
  try {
    for (const conta of ['ML19', 'ML44']) {
      const lista = ALVOS.filter(a => a.conta === conta);
      for (let i = 0; i < lista.length; i += 5) {
        const lote = lista.slice(i, i + 5);
        res.textContent = 'Atualizando ' + conta + ' (' + (i + lote.length) + ' de ' + lista.length + ')…';
        const d = await json('/api/vendas/preco-aplicar', {conta, confirmacao: CONFIRMACAO, parametros: parametros(), liberar_acima_limite: $('vLiberar').checked,
          itens: lote.map(a => ({mlb: a.mlb, sku: a.sku || '', pedidos: [...new Set(a.pedidos)].slice(0, 40), custo: a.custo, meta: a.meta, imposto_pct: a.imposto_pct, preco_atual_visto: a.info.preco_atual, preco_novo_visto: a.info.preco_novo}))});
        for (const r of d.resultados) RES[conta + '|' + r.mlb] = {status: r.status, mensagem: r.mensagem};
        for (const r of d.resultados) { linhasRes.push(conta + ' ' + r.mlb + ': ' + r.status + ' — ' + r.mensagem + (r.planilha ? ' (planilha de preços: ' + r.planilha + ')' : '') + (r.historico ? ' · ' + r.historico : '')); }
        ok += d.atualizados;
        for (const r of d.resultados) if (r.status === 'OK') for (const l of LINHAS) if (l.conta === conta && l.mlb === r.mlb) SEL.delete(l._i);
      }
    }
    res.className = 'aviso ' + (ok === ALVOS.length ? 'ok' : 'erro'); res.replaceChildren(el('b', {}, ok + ' de ' + ALVOS.length + ' preços atualizados.'), ...linhasRes.map(t => el('div', {}, t)));
  } catch (e) { res.className = 'aviso erro'; res.replaceChildren(el('b', {}, 'Parei: ' + e.message), ...linhasRes.map(t => el('div', {}, t))); }
  INFO = {}; $('vPrecoCancela').disabled = false; $('vPrecoCancela').textContent = 'Fechar'; desenhar();
};

// ---------- trocar SKU e pausar anúncios (escritas manuais; sempre com confirmação) ----------
const anunciosMarcados = () => {
  const por = new Map();
  for (const r of LINHAS) if (SEL.has(r._i) && !por.has(r.conta + '|' + r.mlb)) por.set(r.conta + '|' + r.mlb, {conta: r.conta, mlb: r.mlb, sku: r.sku || '', titulo: r.titulo || ''});
  return [...por.values()];
};
let ALVOS_SKU = [], ALVOS_PAUSAR = [];
const emLotes = (lista, fn) => { const por = {}; for (const a of lista) (por[a.conta] = por[a.conta] || []).push(a); return Promise.all(Object.entries(por).map(async ([conta, l]) => { for (let i = 0; i < l.length; i += 10) await fn(conta, l.slice(i, i + 10)); })); };
$('vSku').onclick = async () => {
  const marcados = anunciosMarcados(); if (!marcados.length) return;
  $('vPainelPausar').hidden = true; $('vSkuEntendi').checked = false; $('vSkuOk').disabled = true; $('vSkuCancela').textContent = 'Cancelar';
  const res = $('vSkuRes'); res.hidden = false; res.className = 'aviso'; res.textContent = 'Lendo no Mercado Livre o SKU de cada variação…';
  $('vSkuLista').replaceChildren(); $('vPainelSku').hidden = false; $('vPainelSku').scrollIntoView({behavior: 'smooth', block: 'center'});
  ALVOS_SKU = [];
  try {
    await emLotes(marcados, async (conta, lote) => {
      const d = await json('/api/vendas/anuncios-skus', {conta, itens: lote.map(a => ({mlb: a.mlb}))});
      for (const a of d.anuncios) {
        const m = lote.find(x => x.mlb === a.mlb);
        if (a.erro) { ALVOS_SKU.push({conta, mlb: a.mlb, titulo: m.titulo, erro: a.erro}); continue; }
        for (const l of a.locais) ALVOS_SKU.push({conta, mlb: a.mlb, titulo: a.titulo || m.titulo, variacao: l.variacao, nome: l.nome, sku: l.sku, campo: el('input', {type: 'text', maxLength: 100, placeholder: l.sku ? 'deixe vazio para não trocar' : 'digite o SKU'})});
      }
    });
    ALVOS_SKU.sort((x, y) => (x.conta + x.mlb).localeCompare(y.conta + y.mlb));
    $('vSkuLista').replaceChildren(...ALVOS_SKU.map(a => a.erro
      ? el('tr', {}, el('td', {}, a.conta), el('td', {}, a.mlb), el('td', {}, a.titulo.slice(0, 40)), el('td', {class: 'erro', colSpan: 3}, a.erro))
      : el('tr', {}, el('td', {}, a.conta), el('td', {}, a.mlb), el('td', {}, a.titulo.slice(0, 40) + (a.nome ? ' — ' + a.nome : '')), el('td', {}, a.sku || '(sem SKU)'), el('td', {}, a.campo))));
    res.hidden = true;
  } catch (e) { res.className = 'aviso erro'; res.textContent = 'Não consegui ler os anúncios: ' + e.message; }
};
$('vSkuEntendi').onchange = () => { $('vSkuOk').disabled = !$('vSkuEntendi').checked; };
$('vSkuCancela').onclick = () => { $('vPainelSku').hidden = true; };
$('vSkuOk').onclick = async () => {
  const lista = ALVOS_SKU.filter(a => !a.erro && a.campo.value.trim() && a.campo.value.trim() !== a.sku);
  const res = $('vSkuRes'); res.hidden = false;
  if (!lista.length) { res.className = 'aviso erro'; res.textContent = 'Digite o SKU novo em pelo menos uma linha.'; return; }
  $('vSkuOk').disabled = true; $('vSkuCancela').disabled = true; res.className = 'aviso'; res.textContent = 'Trocando…';
  const porAnuncio = new Map();
  for (const a of lista) { const k = a.conta + '|' + a.mlb; if (!porAnuncio.has(k)) porAnuncio.set(k, {conta: a.conta, mlb: a.mlb, trocas: []}); porAnuncio.get(k).trocas.push({variacao: a.variacao, sku_atual: a.sku, sku_novo: a.campo.value.trim()}); }
  const linhasRes = []; let ok = 0; const novos = {};
  try {
    await emLotes([...porAnuncio.values()], async (conta, lote) => {
      const d = await json('/api/vendas/sku-trocar', {conta, confirmacao: 'TROCAR_SKU', itens: lote.map(a => ({mlb: a.mlb, trocas: a.trocas}))});
      for (const r of d.resultados) {
        linhasRes.push(conta + ' ' + r.mlb + ': ' + r.status + ' — ' + r.mensagem);
        if (r.status === 'OK') { ok++; for (const t of lote.find(a => a.mlb === r.mlb).trocas) novos[conta + '|' + r.mlb + '|' + t.sku_atual] = t.sku_novo; }
      }
    });
    for (const x of RAW) { const n = novos[x.conta + '|' + x.mlb + '|' + (x.sku || '')]; if (n) x.sku = n; }      // as vendas que usavam o SKU antigo passam a usar o novo
    const faltam = [...new Set(Object.values(novos))].filter(k => !CAD[k]);
    for (let i = 0; i < faltam.length; i += 100) Object.assign(CAD, (await json('/api/vendas/cadastro', {skus: faltam.slice(i, i + 100)})).cadastro);
    if (ok) { await recalcular(); INFO = {}; }
    res.className = 'aviso ' + (ok === porAnuncio.size ? 'ok' : 'erro'); res.replaceChildren(el('b', {}, ok + ' de ' + porAnuncio.size + ' anúncios com SKU trocado (' + lista.length + ' SKUs).' + (ok ? ' Custo e margem recalculados.' : '')), ...linhasRes.map(t => el('div', {}, t)));
  } catch (e) { res.className = 'aviso erro'; res.replaceChildren(el('b', {}, 'Parei: ' + e.message), ...linhasRes.map(t => el('div', {}, t))); }
  $('vSkuCancela').disabled = false; $('vSkuCancela').textContent = 'Fechar'; desenhar();
};
$('vPausar').onclick = () => {
  ALVOS_PAUSAR = anunciosMarcados(); if (!ALVOS_PAUSAR.length) return;
  $('vPausarLista').replaceChildren(...ALVOS_PAUSAR.map(a => el('tr', {}, el('td', {}, a.conta), el('td', {}, a.mlb), el('td', {}, a.titulo.slice(0, 60)), el('td', {}, a.sku || '—'))));
  $('vPausarEntendi').checked = false; $('vPausarOk').disabled = true; $('vPausarRes').hidden = true; $('vPausarCancela').textContent = 'Cancelar'; $('vPainelSku').hidden = true;
  $('vPainelPausar').hidden = false; $('vPainelPausar').scrollIntoView({behavior: 'smooth', block: 'center'});
};
$('vPausarEntendi').onchange = () => { $('vPausarOk').disabled = !$('vPausarEntendi').checked; };
$('vPausarCancela').onclick = () => { $('vPainelPausar').hidden = true; };
$('vPausarOk').onclick = async () => {
  $('vPausarOk').disabled = true; $('vPausarCancela').disabled = true;
  const res = $('vPausarRes'); res.hidden = false; res.className = 'aviso'; res.textContent = 'Pausando…';
  const linhasRes = []; let ok = 0;
  try {
    await emLotes(ALVOS_PAUSAR, async (conta, lote) => {
      const d = await json('/api/vendas/pausar', {conta, confirmacao: 'PAUSAR_ANUNCIOS', itens: lote.map(a => ({mlb: a.mlb}))});
      for (const r of d.resultados) { linhasRes.push(conta + ' ' + r.mlb + ': ' + r.status + ' — ' + r.mensagem); if (r.status === 'OK') ok++; }
    });
    res.className = 'aviso ' + (ok === ALVOS_PAUSAR.length ? 'ok' : 'erro'); res.replaceChildren(el('b', {}, ok + ' de ' + ALVOS_PAUSAR.length + ' anúncios pausados.'), ...linhasRes.map(t => el('div', {}, t)));
  } catch (e) { res.className = 'aviso erro'; res.replaceChildren(el('b', {}, 'Parei: ' + e.message), ...linhasRes.map(t => el('div', {}, t))); }
  $('vPausarCancela').disabled = false; $('vPausarCancela').textContent = 'Fechar';
};

// ---------- relatórios ----------
const corpoRelatorio = tipo => ({tipo, dia: DIA, periodo: PERIODO, parametros: parametros(),
  linhas: LINHAS.filter(r => tipo !== 'selecionadas' || SEL.has(r._i)).map(r => ({conta: r.conta, pedido: r.pedido, data: r.data, mlb: r.mlb, sku: r.sku, titulo: r.titulo, grupo: r.grupo, logistica: r.logistica, status_envio: r.status_envio, substatus: r.substatus, status_rotulo: r.status_rotulo, obs: r.obs,
    meta_origem: r.meta_origem, quantidade: r.quantidade, venda: r.venda, tarifa: r.tarifa, frete: r.frete, custo_unit: r.custo_unit, meta: r.meta, nao_enviar: SEL.has(r._i) && podeMarcar(r)}))});
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
$('vBaixarSel').onclick = () => baixar('selecionadas', $('vBaixarSel'));
$('vBaixarNeg').onclick = () => baixar('prejuizo', $('vBaixarNeg'));
$('vSalvar').onclick = async () => {
  const b = $('vSalvar'); b.disabled = true; mensagem('Salvando na planilha do Google…');
  try {
    const d = await json('/api/vendas/salvar', corpoRelatorio('salvar'));
    mensagem('Relatório salvo na planilha: ' + d.abas.join(', ') + '.', 'ok');
  } catch (err) { mensagem(err.message, 'erro'); }
  b.disabled = false;
};
$('vBuscar').onclick = () => buscar('periodo');
$('vEnviar').onclick = () => buscar('enviar');

// ---------- resumo do que ficou para fazer na mão ----------
function pendenciasManuais(){
  const vistos = new Map();
  for (const r of LINHAS) {
    if (!['prejuizo', 'abaixo', 'sem_dados'].includes(r.classe)) continue;
    const k = r.conta + '|' + r.mlb; if (vistos.has(k)) continue;
    const base = {conta: r.conta, mlb: r.mlb, sku: r.sku || '', produto: r.titulo, situacao: ROT[r.classe], preco_atual: null, preco_necessario: null};
    let item = null;
    if (r.classe === 'sem_dados') item = Object.assign(base, {tipo: 'Não calculado', motivo: 'Faltam dados: ' + (r.pendencias || '—') + '. Digite o que falta na linha ou ajuste o preço direto no Mercado Livre.'});
    else {
      const i = infoDe(r), res = RES[k];
      if (res && res.status === 'OK') continue;
      if (res) item = Object.assign(base, {tipo: 'Não atualizado', motivo: res.mensagem + ' Ajuste direto no Mercado Livre.', preco_atual: i && i.preco_atual, preco_necessario: i && i.preco_novo});
      else if (!r.custo_unit || r.imposto_pct === null || r.imposto_pct === undefined) item = Object.assign(base, {tipo: 'Não calculado', motivo: 'Falta custo ou imposto para calcular o preço. Digite na linha.'});
      else if (!i) item = Object.assign(base, {tipo: 'Não calculado', motivo: 'O preço necessário ainda está sendo calculado.'});
      else if (i.acao === 'bloqueado') item = Object.assign(base, {tipo: 'Não calculado', motivo: i.mensagem + ' Ajuste o preço direto no Mercado Livre.', preco_atual: i.preco_atual});
      else if (i.acao === 'subir') item = Object.assign(base, {tipo: 'Ainda não atualizado', motivo: 'Preço sugerido calculado. Marque e atualize na tela, ou ajuste direto no Mercado Livre.', preco_atual: i.preco_atual, preco_necessario: i.preco_novo});
      else continue;     // 'ok': o anúncio já atinge a margem, nada a fazer
    }
    vistos.set(k, item);
  }
  const ordemTipo = {'Não atualizado': 0, 'Não calculado': 1, 'Ainda não atualizado': 2};
  return [...vistos.values()].sort((a, b) => ordemTipo[a.tipo] - ordemTipo[b.tipo]);
}
let MANUAL = [];
function resumoManual(){
  MANUAL = pendenciasManuais();
  const c = {}; MANUAL.forEach(m => c[m.tipo] = (c[m.tipo] || 0) + 1);
  $('vManualTxt').textContent = MANUAL.length ? Object.entries(c).map(([k, n]) => n + ' ' + k.toLowerCase()).join(' · ') + '. Esses anúncios precisam de ajuste de preço direto no Mercado Livre (ou de um dado a mais aqui).' : 'Nada pendente: tudo que estava abaixo da margem já foi calculado e atualizado.';
  $('vManualCorpo').replaceChildren(...MANUAL.map(m => el('tr', {}, el('td', {}, el('span', {class: 'tag ' + (m.tipo === 'Não atualizado' ? 'v-prejuizo' : m.tipo === 'Não calculado' ? 'v-abaixo' : 'v-sem_dados')}, m.tipo)),
    el('td', {}, m.conta), el('td', {}, m.mlb), el('td', {}, m.sku || '—'), el('td', {}, m.produto.slice(0, 60)), el('td', {}, m.situacao), el('td', {class:'n'}, brl(m.preco_atual)), el('td', {class:'n'}, brl(m.preco_necessario)), el('td', {}, m.motivo))));
  $('vManualBaixar').disabled = !MANUAL.length;
}
$('vManualBaixar').onclick = async () => {
  const b = $('vManualBaixar'); b.disabled = true;
  try {
    const resp = await fetch('/api/vendas/exportar-manual', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({periodo: PERIODO, itens: MANUAL})});
    if (resp.status === 401) { location.href = '/login'; return; }
    if (!resp.ok) { let m = 'Erro ' + resp.status; try { m = (await resp.json()).error || m; } catch(e) {} throw new Error(m); }
    const blob = await resp.blob(), a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'fazer_na_mao.xlsx';
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (e) { mensagem('Não consegui gerar o resumo: ' + e.message, 'erro'); }
  b.disabled = !MANUAL.length;
};
