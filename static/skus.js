"use strict";
const $ = id => document.getElementById(id);
const el = (tag, props, ...filhos) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) { if (k === 'class') e.className = v; else e[k] = v; }
  for (const f of filhos) if (f !== null && f !== undefined) e.append(f instanceof Node ? f : String(f));
  return e;
};
const CONFIRMACAO = 'TROCAR_SKU';
const POR_ABA = 500;
const STATUS = {active: 'Ativo', paused: 'Pausado', closed: 'Encerrado', under_review: 'Em revisão', inactive: 'Inativo'};
let LINHAS = [];            // {id, conta, mlb, titulo, status, variacao, sku_atual, sku_novo, aviso, permitido, link, marcado, resultado}
let REGRA = null, PARAR = false, BUSCANDO = false, TROCANDO = false, TXT_TROCA = '', ABA = 1;   // a lista é mostrada em abas de 500 anúncios (pode ter milhares); filtro, marcar-todos e preencher valem para todas as abas

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
  if (BUSCANDO || TROCANDO) return;
  const regra = lerRegra();
  if (regra.modo !== 'sem_todos') {
    if (!regra.termo) { mensagem(SEM_SKU(regra.modo) ? 'Digite a palavra que está no título do produto.' : 'Digite o SKU que será procurado.', 'erro'); return; }
    if (!regra.novo) { mensagem('Digite o SKU novo.', 'erro'); return; }
    if (regra.modo !== 'igual' && regra.termo.length < 2) { mensagem('Digite pelo menos 2 caracteres para buscar por "contém" ou "começa com".', 'erro'); return; }
  }
  const contas = $('sConta').value === 'AMBAS' ? ['ML19', 'ML44'] : [$('sConta').value];
  BUSCANDO = true; PARAR = false; REGRA = regra; LINHAS = []; ABA = 1; mensagem(''); $('sFeitos').hidden = true; $('sFeitosCorpo').replaceChildren();
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
        if (document.activeElement && document.activeElement.closest && document.activeElement.closest('#sCorpo')) resumo(); else desenhar();   // não tira o cursor de quem está digitando um SKU
        if (d.concluido) break;
      }
    }
  } catch (e) { falhou = e; }
  BUSCANDO = false; $('sBuscar').disabled = TROCANDO; $('sParar').hidden = true; progresso(null); desenhar();
  if (falhou) mensagem('A busca parou: ' + falhou.message + ' O que já apareceu continua na tela.', 'erro');
  else if (parou) mensagem('Busca interrompida. A lista mostra só o que foi lido até aqui.');
  else mensagem(LINHAS.length ? 'Busca concluída. Marque os anúncios que quer trocar.' : 'Busca concluída: nenhum anúncio com esse SKU.', LINHAS.length ? 'ok' : '');
}

// filtro da lista (só sobre o que já foi encontrado): ignora maiúsculas e acentos; várias palavras separadas por espaço
const limpa = t => String(t || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
function aparece(l){
  const palavras = limpa($('sFiltro').value).split(/\s+/).filter(Boolean), fora = limpa($('sFiltroNao').value).split(/\s+/).filter(Boolean);
  if (!palavras.length && !fora.length) return true;
  const t = limpa(l.titulo + ' ' + (l.variacao_nome || ''));      // procura no título e no nome da variação (em qualquer um dos dois)
  if (fora.some(p => t.includes(p))) return false;                // "não pode conter": basta UMA dessas palavras para o anúncio sair
  if (!palavras.length) return true;
  return $('sFiltroModo').value === 'qualquer' ? palavras.some(p => t.includes(p)) : palavras.every(p => t.includes(p));
}
function filtrar(){ LINHAS.forEach(l => { if (!aparece(l)) l.marcado = false; }); ABA = 1; desenhar(); }   // o que some da tela nunca fica marcado

function irParaAba(n){ ABA = n; desenhar(); const t = $('sAbas'); if (t && t.scrollIntoView) t.scrollIntoView({block: 'start'}); }
function desenharAbas(n, abas){
  for (const id of ['sAbas', 'sAbas2']) {
    const box = $(id); box.replaceChildren(); box.hidden = n <= POR_ABA;
    if (n <= POR_ABA) continue;
    box.append(el('span', {class: 'suave pequeno'}, 'Anúncios ' + ((ABA - 1) * POR_ABA + 1).toLocaleString('pt-BR') + ' a ' + Math.min(ABA * POR_ABA, n).toLocaleString('pt-BR') + ' de ' + n.toLocaleString('pt-BR') + ' (500 por aba) '));
    const botao = (rot, alvo, ativa) => { const b = el('button', {type: 'button', class: ativa ? 'primario' : '', disabled: alvo === null}, rot); if (alvo !== null) b.addEventListener('click', () => irParaAba(alvo)); box.append(b); };
    const nums = new Set([1, abas]); for (let k = ABA - 3; k <= ABA + 3; k++) if (k >= 1 && k <= abas) nums.add(k);
    botao('‹', ABA > 1 ? ABA - 1 : null, false);
    let ant = 0;
    for (const k of [...nums].sort((x, y) => x - y)) { if (k - ant > 1) box.append(el('span', {class: 'suave'}, '…')); botao(String(k), k, k === ABA); ant = k; }
    botao('›', ABA < abas ? ABA + 1 : null, false);
  }
}
function desenhar(){
  const corpo = $('sCorpo'); corpo.replaceChildren();
  const todas = LINHAS.filter(aparece);
  const abas = Math.max(1, Math.ceil(todas.length / POR_ABA)); if (ABA > abas) ABA = abas; if (ABA < 1) ABA = 1;
  const daAba = todas.slice((ABA - 1) * POR_ABA, ABA * POR_ABA);
  for (const l of daAba) {
    const cx = el('input', {type: 'checkbox', checked: l.marcado, disabled: !l.permitido || TROCANDO || !!(l.resultado && l.resultado.status === 'OK')});
    cx.addEventListener('change', () => { l.marcado = cx.checked; resumo(); });
    const mlb = l.link ? el('a', {href: l.link, target: '_blank', rel: 'noopener'}, l.mlb) : l.mlb;
    let sit = STATUS[l.status] || l.status || '—';
    if (!l.permitido && !l.aviso) sit += ' (não dá para alterar)';
    const celSit = el('td', null, sit);
    if (l.aviso) celSit.append(el('div', {class: 'aviso-linha'}, l.aviso));
    if (l.resultado) celSit.append(el('div', {class: l.resultado.status === 'OK' ? 'ok-linha' : 'aviso-linha'}, l.resultado.status === 'OK' ? 'Trocado e conferido ✔' : l.resultado.mensagem));
    let celNovo;
    if (REGRA && REGRA.modo === 'sem_todos' && l.permitido && !(l.resultado && l.resultado.status === 'OK')) {
      const campo = el('input', {type: 'text', value: l.novo || '', maxLength: 100, placeholder: 'SKU deste anúncio', disabled: TROCANDO});
      campo.addEventListener('input', () => { l.novo = campo.value; if (campo.value.trim() && !l.marcado) { l.marcado = true; cx.checked = true; } resumo(); });   // digitou: marca a linha sozinha
      celNovo = el('td', null, campo);
    } else celNovo = el('td', null, (l.resultado && l.resultado.status === 'OK' && l.novo) ? l.novo : (l.sku_novo || '—'));
    corpo.append(el('tr', null, el('td', null, cx), el('td', null, l.conta), el('td', null, mlb), el('td', null, l.titulo), el('td', null, l.variacao === null ? '—' : String(l.variacao)), el('td', null, l.variacao_nome || (l.variacao === null ? '(anúncio sem variação)' : '—')),
      el('td', null, l.sku_atual || '(sem SKU)'), celNovo, celSit));
  }
  const visiveis = todas.length;
  desenharAbas(visiveis, abas);
  const marcaveis = daAba.filter(l => l.permitido && !(l.resultado && l.resultado.status === 'OK'));
  $('sCabTodos').checked = marcaveis.length > 0 && marcaveis.every(l => l.marcado);
  $('sColNovo').textContent = REGRA && REGRA.modo === 'sem_todos' ? 'SKU novo (digite aqui)' : 'SKU novo';
  $('sVazio').hidden = visiveis > 0 || BUSCANDO;
  $('sVazio').textContent = LINHAS.length ? 'Nenhum anúncio da lista tem essas palavras no título.' : 'Nenhum anúncio encontrado com esse SKU.';
  $('sTabela').hidden = !visiveis;
  resumo();
}

function resumo(){
  const indiv = !!REGRA && REGRA.modo === 'sem_todos';
  $('sMassaCaixa').hidden = !(indiv && LINHAS.length);
  const marcados = LINHAS.filter(l => l.marcado && (!indiv || (l.novo || '').trim())).length, liberados = LINHAS.filter(l => l.permitido).length;
  const vis = LINHAS.filter(aparece), filtrando = vis.length !== LINHAS.length;
  $('sFiltroRes').textContent = filtrando ? 'Aparecem ' + vis.length.toLocaleString('pt-BR') + ' de ' + LINHAS.length.toLocaleString('pt-BR') + ' anúncios encontrados.' : (LINHAS.length ? LINHAS.length.toLocaleString('pt-BR') + ' anúncios encontrados (sem filtro).' : '');
  $('sResumo').textContent = LINHAS.length + ' encontrado(s)' + (filtrando ? ' · ' + vis.length + ' aparecendo no filtro' : '') + ' · ' + liberados + ' liberado(s) para trocar';
  const semSku = indiv ? LINHAS.filter(l => l.marcado && !(l.novo || '').trim()).length : 0;
  $('sMarcados').textContent = String(marcados) + (semSku ? ' (' + semSku + ' marcado(s) sem SKU digitado ficam de fora)' : '');
  $('sAplicar').hidden = !LINHAS.length;
  const digitou = $('sConfirma').value.trim() === 'TROCAR';
  $('sTrocar').disabled = TROCANDO || marcados === 0 || !digitou;
  // diz por que o botão ainda não liga
  $('sDica').textContent = TROCANDO ? (TXT_TROCA || 'Gravando no Mercado Livre… aguarde.')
    : marcados === 0 ? 'Marque pelo menos um anúncio' + (indiv ? ' e preencha o SKU dele' : '') + ' para ligar o botão.'
    : !digitou ? 'Digite TROCAR (só a palavra, em maiúsculas) no campo ao lado para ligar o botão.' : 'Tudo certo: clique no botão para gravar no Mercado Livre.';
}

async function trocar(){
  if (TROCANDO) return;
  const indiv = REGRA && REGRA.modo === 'sem_todos';
  const sel = LINHAS.filter(l => l.marcado && aparece(l) && l.permitido && !(l.resultado && l.resultado.status === 'OK') && (!indiv || (l.novo || '').trim()));
  if (!sel.length || $('sConfirma').value.trim() !== 'TROCAR') return;
  TROCANDO = true; TXT_TROCA = ''; $('sTrocar').disabled = true; $('sBuscar').disabled = true; mensagem('');
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
        TXT_TROCA = 'Trocando ' + (feitos + 1) + ' de ' + total + ' anúncios no Mercado Livre…' + (BUSCANDO ? ' (a busca continua rodando)' : ''); $('sDica').textContent = TXT_TROCA;
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
  TROCANDO = false; $('sBuscar').disabled = BUSCANDO; $('sConfirma').value = ''; desenhar();
  const ok = LINHAS.filter(l => l.resultado && l.resultado.status === 'OK').length;
  if (falhou) mensagem('Parou no meio: ' + falhou.message + ' Faça a busca de novo para ver o que já foi trocado antes de repetir.', 'erro');
  else mensagem(ok + ' local(is) com SKU trocado e conferido. Veja a tabela de resultados abaixo.', ok ? 'ok' : '');
}

$('sModo').addEventListener('change', atualizarForm);
$('sBuscar').addEventListener('click', buscar);
$('sSemSku').addEventListener('click', () => { if (BUSCANDO || TROCANDO) return; if (!SEM_SKU($('sModo').value)) $('sModo').value = 'sem_todos'; atualizarForm(); if ($('sModo').value === 'sem_todos') buscar(); else { $('sTermo').focus(); mensagem('Digite a palavra do título e o SKU novo e clique em Buscar anúncios.'); } });
$('sParar').addEventListener('click', () => { PARAR = true; });
$('sTodos').addEventListener('click', () => { LINHAS.filter(aparece).forEach(l => { if (l.permitido && !(l.resultado && l.resultado.status === 'OK')) l.marcado = true; }); desenhar(); });
$('sFiltroLimpa').addEventListener('click', () => { $('sFiltro').value = ''; $('sFiltroNao').value = ''; filtrar(); $('sFiltro').focus(); });
$('sFiltro').addEventListener('input', filtrar); $('sFiltroNao').addEventListener('input', filtrar); $('sFiltroModo').addEventListener('change', filtrar);
$('sCabTodos').addEventListener('change', e => { LINHAS.filter(aparece).slice((ABA - 1) * POR_ABA, ABA * POR_ABA).forEach(l => { if (l.permitido && !(l.resultado && l.resultado.status === 'OK')) l.marcado = e.target.checked; }); desenhar(); });
$('sTodosAba').addEventListener('click', () => { LINHAS.filter(aparece).slice((ABA - 1) * POR_ABA, ABA * POR_ABA).forEach(l => { if (l.permitido && !(l.resultado && l.resultado.status === 'OK')) l.marcado = true; }); desenhar(); });
$('sNenhum').addEventListener('click', () => { LINHAS.forEach(l => { l.marcado = false; }); desenhar(); });
$('sConfirma').addEventListener('input', resumo);
$('sPreencher').addEventListener('click', () => {
  const v = $('sMassa').value.trim();
  if (!v) { mensagem('Digite o SKU que será colocado nos anúncios marcados.', 'erro'); return; }
  const alvo = LINHAS.filter(l => l.marcado && aparece(l) && l.permitido && !(l.resultado && l.resultado.status === 'OK'));
  if (!alvo.length) { mensagem('Marque primeiro os anúncios que vão receber esse SKU (ou use "Marcar todos (todas as abas)").', 'erro'); return; }
  alvo.forEach(l => { l.novo = v; });
  mensagem('SKU "' + v + '" preenchido em ' + alvo.length + ' anúncio(s). Confira, corrija o que precisar, digite TROCAR e grave.', 'ok'); desenhar();
});
$('sTrocar').addEventListener('click', trocar);
$('sDiagBtn').addEventListener('click', async () => {
  const mlb = $('sDiagMlb').value.trim().toUpperCase(), r = $('sDiagRes');
  if (!/^MLB\d+$/.test(mlb)) { r.textContent = 'Digite o código do anúncio, por exemplo MLB123456789.'; return; }
  $('sDiagBtn').disabled = true; r.textContent = 'Consultando o Mercado Livre…';
  try {
    const d = await json('/api/skus/diagnostico', {conta: $('sDiagConta').value, mlb});
    const lim = d.limite_fotos_anuncio == null ? 'não informado' : d.limite_fotos_anuncio, limv = d.limite_fotos_por_variacao == null ? 'não informado' : d.limite_fotos_por_variacao;
    const ids = a => a === null ? 'não consegui ler' : (a.length ? a.join(', ') : 'nenhuma');
    r.textContent = [
      mlb + ' (' + (d.status || '?') + ')',
      'Fotos do anúncio: ' + d.fotos_do_anuncio + ' · limite da categoria: ' + lim + ' · limite por variação: ' + limv,
      'SKU no anúncio: ' + (d.sku_do_anuncio.seller_custom_field || d.sku_do_anuncio.SELLER_SKU || '(nenhum)'),
      ...d.variacoes.map(v => 'Variação ' + v.id + ' [' + (v.nome || '—') + '] · fotos: ' + v.fotos + ' · campo SKU: ' + (v.seller_custom_field || '(vazio)') + ' · atributo SELLER_SKU: ' + (v.SELLER_SKU || '(vazio)') + ' · produto do usuário ' + (v.user_product_id || '(nenhum)') + ': ' + (v.SKU_no_produto_do_usuario === null ? 'não li' : (v.SKU_no_produto_do_usuario || '(sem SKU)'))),
      'Campos que o Mercado Livre devolveu na 1ª variação: ' + (d.variacoes[0] ? d.variacoes[0].campos.join(', ') : '—'),
      'Variações sem SKU lendo o anúncio completo: ' + ids(d.sem_sku_lendo_o_anuncio_completo),
      'Variações sem SKU como esta tela enxerga na busca: ' + ids(d.sem_sku_como_a_varredura_enxerga)].join('\n');
  } catch (e) { r.textContent = 'Não consegui consultar: ' + e.message; }
  $('sDiagBtn').disabled = false;
});
atualizarForm();

// baixar em Excel o que está na tela (já com o filtro de título); só leitura
async function baixarExcel(){
  const itens = LINHAS.filter(aparece).map(l => ({conta: l.conta, mlb: l.mlb, titulo: l.titulo, variacao: l.variacao, variacao_nome: l.variacao_nome || '', status: STATUS[l.status] || l.status || '', sku_atual: l.sku_atual, sku_novo: (l.novo || l.sku_novo || ''), link: l.link || ''}));
  if (!itens.length) { mensagem('Não há anúncios na lista para baixar.', 'erro'); return; }
  $('sExcel').disabled = true;
  try {
    const busca = REGRA ? ({igual: 'SKU igual a ', contem: 'SKU contém ', comeca: 'SKU começa com ', sem_contem: 'Sem SKU, título contém ', sem_comeca: 'Sem SKU, título começa com ', sem_todos: 'Todos sem SKU'}[REGRA.modo] || '') + (REGRA.modo === 'sem_todos' ? '' : REGRA.termo) : '';
    const filtro = $('sFiltro').value.trim(), semPal = $('sFiltroNao').value.trim();
    const r = await fetch('/api/skus/exportar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({itens, busca: busca + (filtro ? ' | filtro do título/variação: ' + filtro : '') + (semPal ? ' | sem as palavras: ' + semPal : '')})});
    if (r.status === 401) { location.href = '/login'; return; }
    if (!r.ok) { let d = null; try { d = await r.json(); } catch (e) {} throw new Error((d && d.error) || ('Erro ' + r.status)); }
    const blob = await r.blob(), a = el('a', {href: URL.createObjectURL(blob), download: 'anuncios_sem_sku.xlsx'});
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    mensagem(itens.length + ' anúncio(s) na planilha baixada.', 'ok');
  } catch (e) { mensagem('Não consegui baixar: ' + e.message, 'erro'); }
  $('sExcel').disabled = false;
}
$('sExcel').addEventListener('click', baixarExcel);
