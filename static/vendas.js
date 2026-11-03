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

let RAW = [], CAD = {}, LINHAS = [], MARCA = new Set(), PRECO = new Set(), OVR = {}, INFO = {}, PEND = new Set(), IMPOSTOS = {}, RES = {};
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
    UPS = null; SO_FORA = false; $('vUpsRes').textContent = '';
    RAW = []; MARCA = new Set(); PRECO = new Set(); OVR = {}; INFO = {}; PEND = new Set(); RES = {}; IMPOSTOS = {}; VERSAO++;
    const andamento = {}; for (const c of contas) andamento[c] = {off: 0, total: 0};
    const mostra = () => progresso(contas.map(c => c + ': ' + (andamento[c].total ? Math.min(andamento[c].off, andamento[c].total) + ' de ' + andamento[c].total + ' pedidos' : 'lendo…')).join(' · ') + (enviar ? ' (só os que faltam enviar)' : ''),
      5 + 55 * contas.reduce((t, c) => t + (andamento[c].total ? Math.min(1, andamento[c].off / andamento[c].total) : 0), 0) / contas.length);
    const pedir = (conta, off) => comTentativas(() => json('/api/vendas/pedidos', {conta, de, ate, offset: off, somente_a_enviar: enviar, etapa: enviar ? $('vEtapa').value : 'todos', prazo: enviar ? $('vPrazo').value : ''}));
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
    PERIODO = de === ate ? de.split('-').reverse().join('/') : de.split('-').reverse().join('/') + ' a ' + ate.split('-').reverse().join('/'); DIA = ate;
    if (enviar) PERIODO = 'A enviar hoje (pedidos de ' + PERIODO + ')';
    progresso('Calculando o lucro…', 95);
    await recalcular();
    aba = 'mogilar'; sub = 'a_enviar'; classe = 'todas'; busca = ''; $('vBusca').value = '';
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
function visiveis(){
  const q = busca.trim().toLowerCase();
  return LINHAS.filter(r => daAba(r) && noSub(r) && (classe === 'todas' || r.classe === classe) && (!SO_FORA || !UPS || !UPS.has(String(r.pedido))) && (!q || [r.pedido, r.sku, r.titulo, r.mlb].join(' ').toLowerCase().includes(q))).sort(ordem);
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
let UPS = null, SO_FORA = false;
const numerosDe = t => new Set((String(t || '').match(/\d{12,}/g) || []));
function conferirUpseller(){
  const nums = numerosDe($('vUpsTexto').value), r = $('vUpsRes');
  if (!nums.size) { UPS = null; SO_FORA = false; r.textContent = 'Cole os números dos pedidos do UpSeller (com 12 dígitos ou mais).'; desenhar(); return; }
  UPS = nums; SO_FORA = false;
  const nossos = new Set(LINHAS.map(x => String(x.pedido))), fora = [...nossos].filter(n => !nums.has(n)), semAqui = [...nums].filter(n => !nossos.has(n));
  r.replaceChildren(document.createTextNode(fora.length + ' pedido(s) daqui não estão na lista do UpSeller; ' + semAqui.length + ' do UpSeller não vieram aqui' + (semAqui.length ? ' (' + semAqui.slice(0, 30).join(', ') + (semAqui.length > 30 ? '…' : '') + ')' : '') + '. '));
  if (fora.length) { const b = el('button', {class:'secundario', type:'button'}, 'Mostrar só os que não estão no UpSeller'); b.onclick = () => { SO_FORA = !SO_FORA; b.textContent = SO_FORA ? 'Mostrar todos de novo' : 'Mostrar só os que não estão no UpSeller'; desenhar(); }; r.append(b); }
  desenhar();
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
  const marcados = LINHAS.filter(r => MARCA.has(r._i));
  const cartoes = [
    el('div', {class:'card'}, el('b', {}, num(esc.length)), el('span', {}, 'itens vendidos (nesta lista)')),
    el('div', {class:'card destaque'}, el('b', {}, num(c.dentro)), el('span', {}, 'dentro da margem')),
    el('div', {class:'card'}, el('b', {}, num(c.abaixo)), el('span', {}, 'abaixo da margem · deixou de ganhar ' + brl(deixou))),
    el('div', {class:'card risco'}, el('b', {}, num(c.prejuizo)), el('span', {}, 'com prejuízo · perdeu ' + brl(prej))),
    el('div', {class:'card'}, el('b', {}, num(c.sem_dados)), el('span', {}, 'sem dados (digite o custo ou a margem na linha)'))];
  if (aba === 'mogilar') cartoes.push(el('div', {class:'card risco'}, el('b', {}, num(marcados.length)), el('span', {}, 'marcados para não enviar')));
  $('vCartoes').replaceChildren(...cartoes);
  const ab = $('vAbas'); ab.replaceChildren();
  for (const [k, nome, n] of [['mogilar', 'Vendas Mogilar (não Full)', LINHAS.filter(r => r.grupo !== 'full').length], ['full', 'Vendas Full', LINHAS.filter(r => r.grupo === 'full').length]])
    ab.append(el('button', {class: k === aba ? 'on' : '', onclick: () => { aba = k; classe = 'todas'; desenhar(); }}, nome + ' (' + n + ')'));
  const gb = $('vGrupos'); gb.replaceChildren(); gb.hidden = aba === 'full';
  if (aba !== 'full') for (const [k, nome] of [['a_enviar', 'A enviar'], ['enviado', 'Já enviados'], ['todos', 'Todas']])
    gb.append(el('button', {class: k === sub ? 'on' : '', onclick: () => { sub = k; desenhar(); }}, nome + ' (' + (k === 'todos' ? LINHAS.filter(r => r.grupo !== 'full').length : LINHAS.filter(r => r.grupo === k).length) + ')'));
  const cb = $('vClasses'); cb.replaceChildren();
  for (const [k, nome] of [['todas', 'Todas'], ['prejuizo', 'Prejuízo'], ['abaixo', 'Abaixo da margem'], ['dentro', 'Dentro da margem'], ['sem_dados', 'Sem dados']])
    cb.append(el('button', {class: k === classe ? 'on' : '', onclick: () => { classe = k; desenhar(); }}, nome + ' (' + (k === 'todas' ? esc.length : (c[k] || 0)) + ')'));
  cabecalho();
  const lista = visiveis(), corpo = $('vCorpo'); corpo.replaceChildren(...lista.map(linha));
  $('vVazio').hidden = lista.length > 0;
  const th = $('vTodos'); if (th) { const m = lista.filter(podeMarcar); th.checked = m.length > 0 && m.every(r => MARCA.has(r._i)); th.disabled = !m.length; }
  const tp = $('vTodosPreco'); if (tp) { const m = lista.filter(selecionavel); tp.checked = m.length > 0 && m.every(r => PRECO.has(r._i)); tp.disabled = !m.length; }
  const nao = aba === 'mogilar';
  for (const id of ['vMarcarRuins', 'vLimpar', 'vBaixarNao']) $(id).hidden = !nao;
  $('vBaixarNao').disabled = !marcados.length; $('vBaixarNao').textContent = 'Baixar lista “Não enviar” (' + marcados.length + ')';
  $('vMarcarRuins').disabled = !lista.some(r => podeMarcar(r) && (r.classe === 'abaixo' || r.classe === 'prejuizo'));
  const sel = LINHAS.filter(r => PRECO.has(r._i) && selecionavel(r));
  $('vPreco').disabled = !sel.length; $('vPreco').textContent = 'Atualizar preço no Mercado Livre (' + sel.length + ')';
  const negs = LINHAS.filter(r => r.classe === 'prejuizo' || r.classe === 'abaixo').length; $('vBaixarNeg').disabled = !negs; $('vBaixarNeg').textContent = 'Baixar Excel dos prejuízos e abaixo da margem (' + negs + ')';
  resumoManual();
  buscarPrecos();
}
function cabecalho(){
  const t = (txt, cls, dica) => el('th', {class: cls || '', title: dica || ''}, txt);
  const cols = [];
  if (aba === 'mogilar') cols.push(el('th', {class:'chk'}, Object.assign(el('input', {type:'checkbox', id:'vTodos', title:'Marcar/desmarcar “não enviar” em tudo que está na tela'}), {onchange: e => { visiveis().filter(podeMarcar).forEach(r => { if (e.target.checked) MARCA.add(r._i); else MARCA.delete(r._i); }); desenhar(); }})));
  cols.push(t('Situação'), t('Prejuízo / deixou de ganhar', 'n', 'Prejuízo (vermelho) ou quanto deixou de ganhar para chegar na margem mínima'), t('Conta'), t('Pedido'), t('SKU'), t('Status do envio'), t('MLB'), t('Produto'),
    t('Qtd', 'n'), t('Venda', 'n'), t('Tarifa ML', 'n'), t('Frete', 'n'), t('Imposto', 'n'), t('Custo unit.', 'n', 'Digite para corrigir ou preencher (vale para o SKU todo, só nesta tela)'), t('Lucro', 'n'), t('Margem', 'n'),
    t('Margem mínima', 'n', 'Digite para corrigir ou preencher (vale para o SKU todo, só nesta tela)'), t('Preço no ML', 'n'), t('Preço necessário', 'n', 'Preço do anúncio para atingir a margem mínima, com tarifa e frete reais'),
    el('th', {class:'chk', title:'Marcar para atualizar o preço no Mercado Livre'}, Object.assign(el('input', {type:'checkbox', id:'vTodosPreco', title:'Marcar/desmarcar atualização de preço em tudo que pode'}), {onchange: e => { visiveis().filter(selecionavel).forEach(r => { if (e.target.checked) PRECO.add(r._i); else PRECO.delete(r._i); }); desenhar(); }})),
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
  if (aba === 'mogilar') {
    const cb = el('input', {type:'checkbox', checked: MARCA.has(r._i), disabled: !podeMarcar(r), title: podeMarcar(r) ? 'Não enviar este pedido' : 'Só dá para marcar o que ainda será enviado por você'});
    cb.onchange = () => { if (cb.checked) MARCA.add(r._i); else MARCA.delete(r._i); desenhar(); };
    cels.push(el('td', {class:'chk'}, cb));
  }
  const sinal = v => v === null || v === undefined ? '' : (v < 0 ? 'n neg' : 'n pos');
  const info = infoDe(r);
  let precoAtual = r.preco_ml === null || r.preco_ml === undefined ? '—' : brl(r.preco_ml), precoNovo = '—', cbPreco = null;
  if (precisaPreco(r)) {
    if (info) {
      precoAtual = info.preco_atual !== undefined && info.preco_atual !== null ? brl(info.preco_atual) : '—';
      if (info.acao === 'subir') precoNovo = el('span', {class:'neg-claro', title: info.mensagem}, brl(info.preco_novo), el('div', {class:'mlbs'}, '+' + pct(info.variacao_pct) + ' → margem ' + pct(info.margem_nova)));
      else precoNovo = el('span', {class:'mlbs', title: info.mensagem}, info.acao === 'ok' ? 'já atinge a margem' : 'bloqueado: ' + info.mensagem);
    } else precoNovo = el('span', {class:'mlbs'}, PEND.has(chaveInfo(r)) ? 'calculando…' : '…');
  } else if (r.classe === 'sem_dados') precoNovo = el('span', {class:'mlbs'}, /custo|SKU/.test(r.pendencias || '') ? 'digite o custo' : 'falta: ' + (r.pendencias || 'dado'));
  if (selecionavel(r)) {
    cbPreco = el('input', {type:'checkbox', checked: PRECO.has(r._i), title: 'Atualizar o preço deste anúncio no Mercado Livre'});
    cbPreco.onchange = () => { if (cbPreco.checked) PRECO.add(r._i); else PRECO.delete(r._i); desenhar(); };
  }
  cels.push(
    el('td', {}, el('span', {class:'tag v-' + r.classe, title: r.pendencias ? 'Falta: ' + r.pendencias : (r.meta_origem === 'padrão' ? 'Usou a margem mínima padrão (produto sem margem na planilha)' : '')}, ROT[r.classe])),
    el('td', {class: 'n ' + (r.perda_tipo === 'prejuizo' ? 'neg' : '')}, r.perda ? el('span', {}, brl(r.perda), el('div', {class:'mlbs'}, r.perda_tipo === 'prejuizo' ? 'prejuízo' : 'deixou de ganhar')) : '—'),
    el('td', {}, r.conta), el('td', {}, el('span', {class:'pedido-n'}, r.pedido, botaoCopiar(r.pedido)), el('div', {class:'mlbs'}, r.data), UPS && !UPS.has(String(r.pedido)) ? el('div', {}, el('span', {class:'tag v-abaixo', title:'Este pedido não está na lista que você colou do UpSeller'}, 'não está no UpSeller')) : ''), el('td', {}, r.sku || '—'),
    el('td', {}, el('span', {class:'tag e-' + r.grupo, title: r.status_envio ? r.status_envio + (r.substatus ? ' / ' + r.substatus : '') : ''}, r.status_rotulo || '—')),
    el('td', {}, r.mlb),
    el('td', {}, r.titulo.slice(0, 70), r.obs ? el('div', {class:'mlbs'}, r.obs) : ''),
    el('td', {class:'n'}, num(r.quantidade)), el('td', {class:'n'}, brl(r.venda)), el('td', {class:'n'}, brl(r.tarifa)), el('td', {class:'n'}, brl(r.frete)), el('td', {class:'n'}, brl(r.imposto)),
    el('td', {class:'n'}, campoEditavel(r, 'custo')), el('td', {class: sinal(r.lucro)}, brl(r.lucro)), el('td', {class: sinal(r.margem)}, pct(r.margem)),
    el('td', {class:'n'}, campoEditavel(r, 'meta')), el('td', {class:'n'}, precoAtual), el('td', {class:'n'}, precoNovo), el('td', {class:'chk'}, cbPreco), el('td', {}, ENV[r.grupo] || r.grupo));
  return el('tr', {class: MARCA.has(r._i) ? 'naoenviar' : ''}, ...cels);
}
$('vUpsConferir').onclick = conferirUpseller;
$('vUpsLimpar').onclick = () => { $('vUpsTexto').value = ''; UPS = null; SO_FORA = false; $('vUpsRes').textContent = ''; desenhar(); };
$('vBusca').oninput = e => { busca = e.target.value; desenhar(); };
$('vMarcarRuins').onclick = () => {
  const a = visiveis().filter(r => podeMarcar(r) && (r.classe === 'abaixo' || r.classe === 'prejuizo')); a.forEach(r => MARCA.add(r._i)); desenhar();
  mensagem(a.length + ' vendas marcadas para não enviar. Desmarque na lista as que quiser enviar mesmo assim.', 'ok');
};
$('vLimpar').onclick = () => { MARCA = new Set(); desenhar(); mensagem(''); };

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
    if (!PRECO.has(r._i) || !selecionavel(r)) continue;
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
        for (const r of d.resultados) if (r.status === 'OK') for (const l of LINHAS) if (l.conta === conta && l.mlb === r.mlb) PRECO.delete(l._i);
      }
    }
    res.className = 'aviso ' + (ok === ALVOS.length ? 'ok' : 'erro'); res.replaceChildren(el('b', {}, ok + ' de ' + ALVOS.length + ' preços atualizados.'), ...linhasRes.map(t => el('div', {}, t)));
  } catch (e) { res.className = 'aviso erro'; res.replaceChildren(el('b', {}, 'Parei: ' + e.message), ...linhasRes.map(t => el('div', {}, t))); }
  INFO = {}; $('vPrecoCancela').disabled = false; $('vPrecoCancela').textContent = 'Fechar'; desenhar();
};

// ---------- relatórios ----------
const corpoRelatorio = tipo => ({tipo, dia: DIA, periodo: PERIODO, parametros: parametros(),
  linhas: LINHAS.map(r => ({conta: r.conta, pedido: r.pedido, data: r.data, mlb: r.mlb, sku: r.sku, titulo: r.titulo, grupo: r.grupo, logistica: r.logistica, status_envio: r.status_envio, substatus: r.substatus, status_rotulo: r.status_rotulo, obs: r.obs,
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
