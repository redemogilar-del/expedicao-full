"use strict";
const $ = id => document.getElementById(id);
const el = (tag, props, ...filhos) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) { if (k === 'class') e.className = v; else e[k] = v; }
  for (const f of filhos) if (f !== null && f !== undefined) e.append(f instanceof Node ? f : String(f));
  return e;
};
const CONFIRMACAO = 'TROCAR_SKU';
const STATUS = {active: 'Ativo', paused: 'Pausado', closed: 'Encerrado', under_review: 'Em revisão', inactive: 'Inativo'};
let LINHAS = [];            // {id, conta, mlb, titulo, status, variacao, sku_atual, sku_novo, aviso, permitido, link, marcado, resultado}
let REGRA = null, PARAR = false, OCUPADO = false;

function mensagem(texto, tipo){ const m = $('sMsg'); m.textContent = texto || ''; m.hidden = !texto; m.className = 'aviso' + (tipo ? ' ' + tipo : ''); }
function progresso(texto, pct){
  $('sProg').hidden = texto === null; if (texto === null) return;
  $('sBarra').style.width = Math.max(2, Math.min(100, pct || 0)) + '%'; $('sTxt').textContent = texto;
}
async function json(url, corpo){
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corpo)});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sessão expirada'); }
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok || !d || d.ok === false) throw new Error((d && d.error) || ('Erro ' + r.status + '. O servidor pode estar acordando; tente de novo em 1 minuto.'));
  return d;
}
async function comTentativas(fn, n = 3){
  let ultimo;
  for (let i = 0; i < n; i++) { try { return await fn(); } catch (e) { ultimo = e; if (/Sessão expirada/.test(e.message) || /^(Digite|Escolha|Para )/.test(e.message)) throw e; await new Promise(r => setTimeout(r, 1500 * (i + 1))); } }
  throw ultimo;
}
const lerRegra = () => ({modo: $('sModo').value, termo: $('sTermo').value.trim(), novo: $('sNovo').value.trim(), escopo: $('sModo').value === 'igual' ? 'inteiro' : $('sEscopo').value});

function atualizarForm(){
  const parcial = $('sModo').value !== 'igual';
  $('sEscopoCaixa').hidden = !parcial;
  $('sTermo').placeholder = $('sModo').value === 'igual' ? 'ex.: GRANITO_25kg' : ($('sModo').value === 'contem' ? 'ex.: _25kg' : 'ex.: GRANITO');
}

async function buscar(){
  if (OCUPADO) return;
  const regra = lerRegra();
  if (!regra.termo) { mensagem('Digite o SKU que será procurado.', 'erro'); return; }
  if (!regra.novo) { mensagem('Digite o SKU novo.', 'erro'); return; }
  if (regra.modo !== 'igual' && regra.termo.length < 2) { mensagem('Para "contém" e "começa com", digite pelo menos 2 caracteres.', 'erro'); return; }
  const contas = $('sConta').value === 'AMBAS' ? ['ML19', 'ML44'] : [$('sConta').value];
  OCUPADO = true; PARAR = false; REGRA = regra; LINHAS = []; mensagem(''); $('sFeitos').hidden = true; $('sFeitosCorpo').replaceChildren();
  $('sBuscar').disabled = true; $('sParar').hidden = false; $('sResultado').hidden = false; desenhar();
  let falhou = null, parou = false;
  try {
    for (let ci = 0; ci < contas.length && !parou; ci++) {
      const conta = contas[ci];
      let scroll = null, lidos = 0;
      for (;;) {
        if (PARAR) { parou = true; break; }
        const d = await comTentativas(() => json('/api/skus/buscar', Object.assign({conta, scroll_id: scroll}, regra)));
        lidos += d.lidos; scroll = d.scroll_id;
        for (const a of d.achados) LINHAS.push(Object.assign({}, a, {id: LINHAS.length, conta, marcado: false, resultado: null}));
        const pct = d.total ? (lidos / d.total) * 100 : 5;
        progresso(conta + ': leu ' + lidos.toLocaleString('pt-BR') + (d.total ? ' de ' + d.total.toLocaleString('pt-BR') : '') + ' anúncios · ' + LINHAS.length + ' encontrado(s)', (ci * 100 + pct) / contas.length);
        desenhar();
        if (d.concluido) break;
      }
    }
  } catch (e) { falhou = e; }
  OCUPADO = false; $('sBuscar').disabled = false; $('sParar').hidden = true; progresso(null); desenhar();
  if (falhou) mensagem('A busca parou: ' + falhou.message + ' O que já apareceu continua na tela.', 'erro');
  else if (parou) mensagem('Busca interrompida. A lista mostra só o que foi lido até aqui.');
  else mensagem(LINHAS.length ? 'Busca concluída. Marque os anúncios que quer trocar.' : 'Busca concluída: nenhum anúncio com esse SKU.', LINHAS.length ? 'ok' : '');
}

function desenhar(){
  const corpo = $('sCorpo'); corpo.replaceChildren();
  for (const l of LINHAS) {
    const cx = el('input', {type: 'checkbox', checked: l.marcado, disabled: !l.permitido || OCUPADO || !!(l.resultado && l.resultado.status === 'OK')});
    cx.addEventListener('change', () => { l.marcado = cx.checked; resumo(); });
    const mlb = l.link ? el('a', {href: l.link, target: '_blank', rel: 'noopener'}, l.mlb) : l.mlb;
    let sit = STATUS[l.status] || l.status || '—';
    if (!l.permitido && !l.aviso) sit += ' (não dá para alterar)';
    const celSit = el('td', null, sit);
    if (l.aviso) celSit.append(el('div', {class: 'aviso-linha'}, l.aviso));
    if (l.resultado) celSit.append(el('div', {class: l.resultado.status === 'OK' ? 'ok-linha' : 'aviso-linha'}, l.resultado.status === 'OK' ? 'Trocado e conferido ✔' : l.resultado.mensagem));
    corpo.append(el('tr', null, el('td', null, cx), el('td', null, l.conta), el('td', null, mlb), el('td', null, l.titulo), el('td', null, l.variacao === null ? '—' : String(l.variacao)),
      el('td', null, l.sku_atual), el('td', null, l.sku_novo || '—'), celSit));
  }
  $('sVazio').hidden = LINHAS.length > 0 || OCUPADO;
  $('sTabela').hidden = !LINHAS.length;
  resumo();
}

function resumo(){
  const marcados = LINHAS.filter(l => l.marcado).length, liberados = LINHAS.filter(l => l.permitido).length;
  $('sResumo').textContent = LINHAS.length + ' encontrado(s) · ' + liberados + ' liberado(s) para trocar';
  $('sMarcados').textContent = String(marcados);
  $('sAplicar').hidden = !LINHAS.length;
  $('sTrocar').disabled = OCUPADO || marcados === 0 || $('sConfirma').value.trim() !== 'TROCAR';
}

async function trocar(){
  if (OCUPADO) return;
  const sel = LINHAS.filter(l => l.marcado && l.permitido && !(l.resultado && l.resultado.status === 'OK'));
  if (!sel.length || $('sConfirma').value.trim() !== 'TROCAR') return;
  OCUPADO = true; $('sTrocar').disabled = true; $('sBuscar').disabled = true; mensagem('');
  $('sFeitos').hidden = false;
  const porAnuncio = new Map();
  for (const l of sel) { const k = l.conta + '|' + l.mlb; if (!porAnuncio.has(k)) porAnuncio.set(k, []); porAnuncio.get(k).push(l); }
  const lotes = {};
  for (const [k, ls] of porAnuncio) { const c = ls[0].conta; (lotes[c] = lotes[c] || []).push(ls); }
  let feitos = 0, total = porAnuncio.size, falhou = null;
  try {
    for (const conta of Object.keys(lotes)) {
      for (let i = 0; i < lotes[conta].length; i += 5) {
        const grupo = lotes[conta].slice(i, i + 5), itens = [];
        for (const ls of grupo) for (const l of ls) itens.push({mlb: l.mlb, variacao: l.variacao, sku_visto: l.sku_atual});
        progresso('Trocando ' + (feitos + 1) + ' de ' + total + '…', (feitos / total) * 100);
        // sem repetir automaticamente: se der erro, a pessoa busca de novo e confere o que ficou feito
        const d = await json('/api/skus/aplicar', Object.assign({conta, confirmacao: CONFIRMACAO, itens}, REGRA));
        for (const r of d.resultados) {
          for (const l of grupo.flat().filter(x => x.mlb === r.mlb)) { l.resultado = r; if (r.status === 'OK') l.marcado = false; }
          $('sFeitosCorpo').append(el('tr', null, el('td', null, conta), el('td', null, r.mlb), el('td', null, r.status === 'OK' ? 'Trocado ✔' : (r.status === 'PULADO' ? 'Não alterado' : (r.status === 'NAO_CONFIRMADO' ? 'Confira no Mercado Livre' : 'Erro'))), el('td', null, r.mensagem)));
        }
        feitos += grupo.length; desenhar();
      }
    }
  } catch (e) { falhou = e; }
  OCUPADO = false; $('sBuscar').disabled = false; progresso(null); $('sConfirma').value = ''; desenhar();
  const ok = LINHAS.filter(l => l.resultado && l.resultado.status === 'OK').length;
  if (falhou) mensagem('Parou no meio: ' + falhou.message + ' Faça a busca de novo para ver o que já foi trocado antes de repetir.', 'erro');
  else mensagem(ok + ' local(is) com SKU trocado e conferido. Veja a tabela de resultados abaixo.', ok ? 'ok' : '');
}

$('sModo').addEventListener('change', atualizarForm);
$('sBuscar').addEventListener('click', buscar);
$('sParar').addEventListener('click', () => { PARAR = true; });
$('sTodos').addEventListener('click', () => { LINHAS.forEach(l => { if (l.permitido && !(l.resultado && l.resultado.status === 'OK')) l.marcado = true; }); desenhar(); });
$('sNenhum').addEventListener('click', () => { LINHAS.forEach(l => { l.marcado = false; }); desenhar(); });
$('sConfirma').addEventListener('input', resumo);
$('sTrocar').addEventListener('click', trocar);
atualizarForm();
