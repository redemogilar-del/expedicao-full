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
let REGRA = null, PARAR = false, OCUPADO = false, MOSTRAR = 300;   // MOSTRAR: quantas linhas desenhar (a lista pode ter milhares; filtro e marcar-todos valem para todas)

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

const SEM_SKU = m => m.startsWith('sem_');
function atualizarForm(){
  const m = $('sModo').value, sem = SEM_SKU(m);
  $('sEscopoCaixa').hidden = sem || m === 'igual';
  $('sTermoRot').hidden = m === 'sem_todos'; $('sNovoCaixa').hidden = m === 'sem_todos';
  $('sTermoTxt').textContent = sem ? 'Palavra do título' : 'SKU procurado';
  $('sTermo').placeholder = sem ? 'ex.: granito' : (m === 'igual' ? 'ex.: GRANITO_25kg' : (m === 'contem' ? 'ex.: _25kg' : 'ex.: GRANITO'));
  $('sBuscar').textContent = m === 'sem_todos' ? 'Ver todos sem SKU' : 'Buscar anúncios';
}

async function buscar(){
  if (OCUPADO) return;
  const regra = lerRegra();
  if (regra.modo !== 'sem_todos') {
    if (!regra.termo) { mensagem(SEM_SKU(regra.modo) ? 'Digite a palavra que está no título do produto.' : 'Digite o SKU que será procurado.', 'erro'); return; }
    if (!regra.novo) { mensagem('Digite o SKU novo.', 'erro'); return; }
    if (regra.modo !== 'igual' && regra.termo.length < 2) { mensagem('Digite pelo menos 2 caracteres para buscar por "contém" ou "começa com".', 'erro'); return; }
  }
  const contas = $('sConta').value === 'AMBAS' ? ['ML19', 'ML44'] : [$('sConta').value];
  OCUPADO = true; PARAR = false; REGRA = regra; LINHAS = []; MOSTRAR = 300; mensagem(''); $('sFeitos').hidden = true; $('sFeitosCorpo').replaceChildren();
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

// filtro da lista (só sobre o que já foi encontrado): ignora maiúsculas e acentos; várias palavras separadas por espaço
const limpa = t => String(t || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
function aparece(l){
  const palavras = limpa($('sFiltro').value).split(/\s+/).filter(Boolean);
  if (!palavras.length) return true;
  const t = limpa(l.titulo);
  return $('sFiltroModo').value === 'qualquer' ? palavras.some(p => t.includes(p)) : palavras.every(p => t.includes(p));
}
function filtrar(){ LINHAS.forEach(l => { if (!aparece(l)) l.marcado = false; }); MOSTRAR = 300; desenhar(); }   // o que some da tela nunca fica marcado

function desenhar(){
  const corpo = $('sCorpo'); corpo.replaceChildren();
  const todas = LINHAS.filter(aparece);
  for (const l of todas.slice(0, MOSTRAR)) {
    const cx = el('input', {type: 'checkbox', checked: l.marcado, disabled: !l.permitido || OCUPADO || !!(l.resultado && l.resultado.status === 'OK')});
    cx.addEventListener('change', () => { l.marcado = cx.checked; resumo(); });
    const mlb = l.link ? el('a', {href: l.link, target: '_blank', rel: 'noopener'}, l.mlb) : l.mlb;
    let sit = STATUS[l.status] || l.status || '—';
    if (!l.permitido && !l.aviso) sit += ' (não dá para alterar)';
    const celSit = el('td', null, sit);
    if (l.aviso) celSit.append(el('div', {class: 'aviso-linha'}, l.aviso));
    if (l.resultado) celSit.append(el('div', {class: l.resultado.status === 'OK' ? 'ok-linha' : 'aviso-linha'}, l.resultado.status === 'OK' ? 'Trocado e conferido ✔' : l.resultado.mensagem));
    let celNovo;
    if (REGRA && REGRA.modo === 'sem_todos' && l.permitido && !(l.resultado && l.resultado.status === 'OK')) {
      const campo = el('input', {type: 'text', value: l.novo || '', maxLength: 100, placeholder: 'SKU deste anúncio', disabled: OCUPADO});
      campo.addEventListener('input', () => { l.novo = campo.value; if (campo.value.trim() && !l.marcado) { l.marcado = true; cx.checked = true; } resumo(); });   // digitou: marca a linha sozinha
      celNovo = el('td', null, campo);
    } else celNovo = el('td', null, (l.resultado && l.resultado.status === 'OK' && l.novo) ? l.novo : (l.sku_novo || '—'));
    corpo.append(el('tr', null, el('td', null, cx), el('td', null, l.conta), el('td', null, mlb), el('td', null, l.titulo), el('td', null, l.variacao === null ? '—' : String(l.variacao)),
      el('td', null, l.sku_atual || '(sem SKU)'), celNovo, celSit));
  }
  const visiveis = todas.length;
  $('sLimite').hidden = visiveis <= MOSTRAR;
  if (visiveis > MOSTRAR) { $('sLimite').replaceChildren('Mostrando ' + MOSTRAR + ' de ' + visiveis.toLocaleString('pt-BR') + ' na tela (para a tela não ficar pesada). "Marcar todos" e "Preencher os marcados" valem para todos os ' + visiveis.toLocaleString('pt-BR') + '. ', el('button', {onclick: () => { MOSTRAR += 500; desenhar(); }}, 'Mostrar mais 500')); }
  const marcaveis = todas.filter(l => l.permitido && !(l.resultado && l.resultado.status === 'OK'));
  $('sCabTodos').checked = marcaveis.length > 0 && marcaveis.every(l => l.marcado);
  $('sColNovo').textContent = REGRA && REGRA.modo === 'sem_todos' ? 'SKU novo (digite aqui)' : 'SKU novo';
  $('sVazio').hidden = visiveis > 0 || OCUPADO;
  $('sVazio').textContent = LINHAS.length ? 'Nenhum anúncio da lista tem essas palavras no título.' : 'Nenhum anúncio encontrado com esse SKU.';
  $('sTabela').hidden = !visiveis;
  resumo();
}

function resumo(){
  const indiv = !!REGRA && REGRA.modo === 'sem_todos';
  $('sMassaCaixa').hidden = !(indiv && LINHAS.length);
  const marcados = LINHAS.filter(l => l.marcado && (!indiv || (l.novo || '').trim())).length, liberados = LINHAS.filter(l => l.permitido).length;
  const vis = LINHAS.filter(aparece), filtrando = vis.length !== LINHAS.length;
  $('sResumo').textContent = LINHAS.length + ' encontrado(s)' + (filtrando ? ' · ' + vis.length + ' aparecendo no filtro' : '') + ' · ' + liberados + ' liberado(s) para trocar';
  const semSku = indiv ? LINHAS.filter(l => l.marcado && !(l.novo || '').trim()).length : 0;
  $('sMarcados').textContent = String(marcados) + (semSku ? ' (' + semSku + ' marcado(s) sem SKU digitado ficam de fora)' : '');
  $('sAplicar').hidden = !LINHAS.length;
  $('sTrocar').disabled = OCUPADO || marcados === 0 || $('sConfirma').value.trim() !== 'TROCAR';
}

async function trocar(){
  if (OCUPADO) return;
  const indiv = REGRA && REGRA.modo === 'sem_todos';
  const sel = LINHAS.filter(l => l.marcado && aparece(l) && l.permitido && !(l.resultado && l.resultado.status === 'OK') && (!indiv || (l.novo || '').trim()));
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
        for (const ls of grupo) for (const l of ls) itens.push({mlb: l.mlb, variacao: l.variacao, sku_visto: l.sku_atual, sku_novo: indiv ? l.novo.trim() : undefined});
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
$('sSemSku').addEventListener('click', () => { if (OCUPADO) return; if (!SEM_SKU($('sModo').value)) $('sModo').value = 'sem_todos'; atualizarForm(); if ($('sModo').value === 'sem_todos') buscar(); else { $('sTermo').focus(); mensagem('Digite a palavra do título e o SKU novo e clique em Buscar anúncios.'); } });
$('sParar').addEventListener('click', () => { PARAR = true; });
$('sTodos').addEventListener('click', () => { LINHAS.filter(aparece).forEach(l => { if (l.permitido && !(l.resultado && l.resultado.status === 'OK')) l.marcado = true; }); desenhar(); });
$('sFiltro').addEventListener('input', filtrar); $('sFiltroModo').addEventListener('change', filtrar);
$('sCabTodos').addEventListener('change', e => { LINHAS.filter(aparece).forEach(l => { if (l.permitido && !(l.resultado && l.resultado.status === 'OK')) l.marcado = e.target.checked; }); desenhar(); });
$('sNenhum').addEventListener('click', () => { LINHAS.forEach(l => { l.marcado = false; }); desenhar(); });
$('sConfirma').addEventListener('input', resumo);
$('sPreencher').addEventListener('click', () => {
  const v = $('sMassa').value.trim();
  if (!v) { mensagem('Digite o SKU que será colocado nos anúncios marcados.', 'erro'); return; }
  const alvo = LINHAS.filter(l => l.marcado && aparece(l) && l.permitido && !(l.resultado && l.resultado.status === 'OK'));
  if (!alvo.length) { mensagem('Marque primeiro os anúncios que vão receber esse SKU (ou use "Marcar todos os que aparecem").', 'erro'); return; }
  alvo.forEach(l => { l.novo = v; });
  mensagem('SKU "' + v + '" preenchido em ' + alvo.length + ' anúncio(s). Confira, corrija o que precisar, digite TROCAR e grave.', 'ok'); desenhar();
});
$('sTrocar').addEventListener('click', trocar);
atualizarForm();

// baixar em Excel o que está na tela (já com o filtro de título); só leitura
async function baixarExcel(){
  const itens = LINHAS.filter(aparece).map(l => ({conta: l.conta, mlb: l.mlb, titulo: l.titulo, variacao: l.variacao, status: STATUS[l.status] || l.status || '', sku_atual: l.sku_atual, sku_novo: (l.novo || l.sku_novo || ''), link: l.link || ''}));
  if (!itens.length) { mensagem('Não há anúncios na lista para baixar.', 'erro'); return; }
  $('sExcel').disabled = true;
  try {
    const busca = REGRA ? ({igual: 'SKU igual a ', contem: 'SKU contém ', comeca: 'SKU começa com ', sem_contem: 'Sem SKU, título contém ', sem_comeca: 'Sem SKU, título começa com ', sem_todos: 'Todos sem SKU'}[REGRA.modo] || '') + (REGRA.modo === 'sem_todos' ? '' : REGRA.termo) : '';
    const filtro = $('sFiltro').value.trim();
    const r = await fetch('/api/skus/exportar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({itens, busca: busca + (filtro ? ' | filtro do título: ' + filtro : '')})});
    if (r.status === 401) { location.href = '/login'; return; }
    if (!r.ok) { let d = null; try { d = await r.json(); } catch (e) {} throw new Error((d && d.error) || ('Erro ' + r.status)); }
    const blob = await r.blob(), a = el('a', {href: URL.createObjectURL(blob), download: 'anuncios_sem_sku.xlsx'});
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    mensagem(itens.length + ' anúncio(s) na planilha baixada.', 'ok');
  } catch (e) { mensagem('Não consegui baixar: ' + e.message, 'erro'); }
  $('sExcel').disabled = false;
}
$('sExcel').addEventListener('click', baixarExcel);
