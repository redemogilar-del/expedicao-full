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
const SEM_SKU = m => m.startsWith('sem_');
const POR_TITULO = m => SEM_SKU(m) || m.startsWith('tit_');   // nesses modos o SKU novo é digitado na linha (ou preenchido em massa), não na regra
const INDIVIDUAL = () => !!REGRA && POR_TITULO(REGRA.modo);
const OK_FEITO = l => !!(l.resultado && l.resultado.status === 'OK');
const PRONTA = l => { const n = (l.novo || '').trim(); return !!n && n !== (l.sku_atual || ''); };   // tem SKU digitado e ele é diferente do atual
const lerRegra = () => { const m = $('sModo').value; return {modo: m, termo: $('sTermo').value.trim(), novo: POR_TITULO(m) ? '' : $('sNovo').value.trim(), escopo: m === 'igual' || POR_TITULO(m) ? 'inteiro' : $('sEscopo').value}; };

function atualizarForm(){
  const m = $('sModo').value, tit = POR_TITULO(m);
  $('sEscopoCaixa').hidden = tit || m === 'igual';
  $('sTermoRot').hidden = m === 'sem_todos'; $('sNovoCaixa').hidden = tit;
  $('sTermoTxt').textContent = !tit ? 'SKU procurado' : m.endsWith('_exato') ? 'Título exato do anúncio' : m.endsWith('_comeca') ? 'Começo do título' : 'Palavras do título';
  $('sTermo').placeholder = tit ? (m.endsWith('_exato') ? 'ex.: Granito Grafiato Branco 25kg' : m.endsWith('_comeca') ? 'ex.: Granito' : 'ex.: granito 25kg') : (m === 'igual' ? 'ex.: GRANITO_25kg' : (m === 'contem' ? 'ex.: _25kg' : 'ex.: GRANITO'));
  $('sBuscar').textContent = m === 'sem_todos' ? 'Ver todos sem SKU' : 'Buscar anúncios';
}

async function buscar(){
  if (BUSCANDO || TROCANDO) return;
  const regra = lerRegra();
  if (regra.modo !== 'sem_todos') {
    if (!regra.termo) { mensagem(POR_TITULO(regra.modo) ? 'Digite o que está no título do anúncio.' : 'Digite o SKU que será procurado.', 'erro'); return; }
    if (!POR_TITULO(regra.modo) && !regra.novo) { mensagem('Digite o SKU novo.', 'erro'); return; }
    if (regra.modo !== 'igual' && regra.termo.length < 2) { mensagem('Digite pelo menos 2 caracteres para buscar.', 'erro'); return; }
  }
  const contas = $('sConta').value === 'AMBAS' ? ['ML19', 'ML44'] : [$('sConta').value];
  BUSCANDO = true; PARAR = false; REGRA = regra; LINHAS = []; ABA = 1; mensagem(''); $('sFeitos').hidden = true; $('sFeitosCorpo').replaceChildren();
  $('sBuscar').disabled = true; $('sParar').hidden = false; $('sResultado').hidden = false; desenhar();
  let falhou = null, parou = false;
  const andamento = {};                                   // por conta: {lidos, total}; as contas são lidas ao mesmo tempo
  const textoProgresso = () => {
    const cs = Object.keys(andamento), lidosT = cs.reduce((t, c) => t + andamento[c].lidos, 0), totalT = cs.reduce((t, c) => t + andamento[c].total, 0);
    progresso(cs.map(c => c + ': ' + andamento[c].lidos.toLocaleString('pt-BR') + (andamento[c].total ? ' de ' + andamento[c].total.toLocaleString('pt-BR') : '')).join(' · ') + ' anúncios lidos · ' + LINHAS.length + ' encontrado(s)', totalT ? (lidosT / totalT) * 100 : 5);
  };
  const lerConta = async conta => {
    let scroll = null;
    andamento[conta] = {lidos: 0, total: 0};
    try {
      for (;;) {
        if (PARAR) { parou = true; break; }
        const d = await comTentativas(() => json('/api/skus/buscar', Object.assign({conta, scroll_id: scroll}, regra)));
        scroll = d.scroll_id; andamento[conta].lidos += d.lidos; andamento[conta].total = d.total || andamento[conta].total;
        for (const a of d.achados) LINHAS.push(Object.assign({}, a, {id: LINHAS.length, conta, marcado: false, resultado: null, novo: POR_TITULO(regra.modo) ? (a.sku_atual || '') : undefined}));
        textoProgresso();
        if (document.activeElement && document.activeElement.closest && document.activeElement.closest('#sCorpo')) resumo(); else desenhar();   // não tira o cursor de quem está digitando um SKU
        if (d.concluido) break;
      }
    } catch (e) { falhou = falhou || e; PARAR = true; }    // se uma conta falha, a outra para também (evita lista pela metade sem aviso)
  };
  await Promise.all(contas.map(lerConta));
  BUSCANDO = false; $('sBuscar').disabled = TROCANDO; $('sParar').hidden = true; progresso(null); desenhar();
  if (falhou) mensagem('A busca parou: ' + falhou.message + ' O que já apareceu continua na tela.', 'erro');
  else if (parou) mensagem('Busca interrompida. A lista mostra só o que foi lido até aqui.');
  else mensagem(LINHAS.length ? 'Busca concluída. Marque os anúncios que quer trocar.' : 'Busca concluída: nenhum anúncio encontrado.', LINHAS.length ? 'ok' : '');
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
    let sit = STATUS[l.status] || l.status || '—';
    if (!l.permitido && !l.aviso) sit += ' (não dá para alterar)';
    const celSit = el('td', null, sit);
    if (l.aviso) celSit.append(el('div', {class: 'aviso-linha'}, l.aviso));
    if (l.resultado) celSit.append(el('div', {class: l.resultado.status === 'OK' ? 'ok-linha' : 'aviso-linha'}, l.resultado.status === 'OK' ? 'Trocado e conferido ✔' : l.resultado.mensagem));
    let celNovo;
    if (INDIVIDUAL() && l.permitido && !OK_FEITO(l)) {
      const campo = el('input', {type: 'text', value: l.novo || '', maxLength: 100, placeholder: 'SKU deste anúncio', disabled: TROCANDO, class: PRONTA(l) ? 'editado' : ''});
      campo.addEventListener('input', () => { l.novo = campo.value; campo.classList.toggle('editado', PRONTA(l)); if (PRONTA(l) && !l.marcado) { l.marcado = true; cx.checked = true; } resumo(); });   // digitou um SKU diferente: marca a linha sozinha
      celNovo = el('td', null, campo);
    } else celNovo = el('td', null, (l.resultado && l.resultado.status === 'OK' && l.novo) ? l.novo : (l.sku_novo || '—'));
    corpo.append(el('tr', null, el('td', null, cx), el('td', null, l.conta), celulaMlb(l), el('td', null, l.titulo), el('td', null, l.variacao === null ? '—' : String(l.variacao)), el('td', null, l.variacao_nome || (l.variacao === null ? '(anúncio sem variação)' : '—')),
      el('td', null, l.sku_atual || '(sem SKU)'), celNovo, celSit));
  }
  const visiveis = todas.length;
  desenharAbas(visiveis, abas);
  const marcaveis = daAba.filter(l => l.permitido && !(l.resultado && l.resultado.status === 'OK'));
  $('sCabTodos').checked = marcaveis.length > 0 && marcaveis.every(l => l.marcado);
  $('sColNovo').textContent = INDIVIDUAL() ? 'SKU (edite aqui)' : 'SKU novo';
  $('sVazio').hidden = visiveis > 0 || BUSCANDO;
  $('sVazio').textContent = LINHAS.length ? 'Nenhum anúncio da lista tem essas palavras no título.' : 'Nenhum anúncio encontrado.';
  $('sTabela').hidden = !visiveis;
  resumo();
}

function resumo(){
  const indiv = INDIVIDUAL();
  $('sMassaCaixa').hidden = !(indiv && LINHAS.length);
  const marcados = LINHAS.filter(l => l.marcado && (!indiv || PRONTA(l))).length, liberados = LINHAS.filter(l => l.permitido).length;
  const vis = LINHAS.filter(aparece), filtrando = vis.length !== LINHAS.length;
  $('sFiltroRes').textContent = filtrando ? 'Aparecem ' + vis.length.toLocaleString('pt-BR') + ' de ' + LINHAS.length.toLocaleString('pt-BR') + ' anúncios encontrados.' : (LINHAS.length ? LINHAS.length.toLocaleString('pt-BR') + ' anúncios encontrados (sem filtro).' : '');
  $('sResumo').textContent = LINHAS.length + ' encontrado(s)' + (filtrando ? ' · ' + vis.length + ' aparecendo no filtro' : '') + ' · ' + liberados + ' liberado(s) para trocar';
  const semSku = indiv ? LINHAS.filter(l => l.marcado && !PRONTA(l)).length : 0;
  const substitui = indiv ? LINHAS.filter(l => l.marcado && PRONTA(l) && l.sku_atual).length : 0;
  $('sMarcados').textContent = String(marcados) + (substitui ? ' (' + substitui + ' vão substituir um SKU que já existe)' : '') + (semSku ? ' (' + semSku + ' marcado(s) sem SKU novo ficam de fora)' : '');
  $('sAplicar').hidden = !LINHAS.length;
  const digitou = $('sConfirma').value.trim() === 'TROCAR';
  $('sTrocar').disabled = TROCANDO || marcados === 0 || !digitou;
  // diz por que o botão ainda não liga
  $('sDica').textContent = TROCANDO ? (TXT_TROCA || 'Gravando no Mercado Livre… aguarde.')
    : marcados === 0 ? 'Marque pelo menos um anúncio' + (indiv ? ' e digite o SKU novo dele' : '') + ' para ligar o botão.'
    : !digitou ? 'Digite TROCAR (só a palavra, em maiúsculas) no campo ao lado para ligar o botão.' : 'Tudo certo: clique no botão para gravar no Mercado Livre.';
}

async function trocar(){
  if (TROCANDO) return;
  const indiv = INDIVIDUAL();
  const sel = LINHAS.filter(l => l.marcado && aparece(l) && l.permitido && !OK_FEITO(l) && (!indiv || PRONTA(l)));
  if (!sel.length || $('sConfirma').value.trim() !== 'TROCAR') return;
  TROCANDO = true; TXT_TROCA = ''; $('sTrocar').disabled = true; $('sBuscar').disabled = true; mensagem('');
  $('sFeitos').hidden = false;
  const porAnuncio = new Map();
  for (const l of sel) { const k = l.conta + '|' + l.mlb; if (!porAnuncio.has(k)) porAnuncio.set(k, []); porAnuncio.get(k).push(l); }
  const lotes = {};
  for (const [k, ls] of porAnuncio) { const c = ls[0].conta; (lotes[c] = lotes[c] || []).push(ls); }
  let feitos = 0, total = porAnuncio.size, falhou = null, parar = false;
  const gravarConta = async conta => {                    // ML19 e ML44 são gravadas ao mesmo tempo; dentro de cada conta, de 10 em 10 (o servidor troca os 10 ao mesmo tempo)
    try {
      for (let i = 0; i < lotes[conta].length && !parar; i += 10) {
        const grupo = lotes[conta].slice(i, i + 10), itens = [];
        for (const ls of grupo) for (const l of ls) itens.push({mlb: l.mlb, variacao: l.variacao, sku_visto: l.sku_atual, sku_novo: indiv ? l.novo.trim() : undefined});
        TXT_TROCA = 'Trocando ' + (feitos + 1) + ' de ' + total + ' anúncios no Mercado Livre…' + (BUSCANDO ? ' (a busca continua rodando)' : ''); $('sDica').textContent = TXT_TROCA;
        // sem repetir automaticamente: se der erro, a pessoa busca de novo e confere o que ficou feito
        const d = await json('/api/skus/aplicar', Object.assign({conta, confirmacao: CONFIRMACAO, itens}, REGRA));
        for (const r of d.resultados) {
          for (const l of grupo.flat().filter(x => x.mlb === r.mlb)) { l.resultado = r; if (r.status === 'OK') l.marcado = false; }
          $('sFeitosCorpo').append(el('tr', null, el('td', null, conta), el('td', null, r.mlb), el('td', null, r.status === 'OK' ? 'Trocado ✔' : (r.status === 'PULADO' ? 'Não alterado' : (r.status === 'NAO_CONFIRMADO' ? 'Confira no Mercado Livre' : 'Erro'))), el('td', null, r.mensagem + (r.trocas || []).map(t => ' ' + t.sku_antes + ' → ' + (t.sku_depois || '(vazio)') + '.').join(''))));
        }
        feitos += grupo.length; desenhar();
      }
    } catch (e) { falhou = falhou || e; parar = true; }   // se uma conta falha, a outra termina o lote em andamento e para
  };
  await Promise.all(Object.keys(lotes).map(gravarConta));
  TROCANDO = false; $('sBuscar').disabled = BUSCANDO; $('sConfirma').value = ''; desenhar();
  const ok = LINHAS.filter(l => l.resultado && l.resultado.status === 'OK').length;
  if (falhou) mensagem('Parou no meio: ' + falhou.message + ' Faça a busca de novo para ver o que já foi trocado antes de repetir.', 'erro');
  else mensagem(ok + ' local(is) com SKU trocado e conferido. Veja a tabela de resultados abaixo.', ok ? 'ok' : '');
}

$('sModo').addEventListener('change', atualizarForm);
$('sBuscar').addEventListener('click', buscar);
$('sSemSku').addEventListener('click', () => { if (BUSCANDO || TROCANDO) return; if (!SEM_SKU($('sModo').value)) $('sModo').value = 'sem_todos'; atualizarForm(); if ($('sModo').value === 'sem_todos' || $('sTermo').value.trim()) buscar(); else { $('sTermo').focus(); mensagem('Digite o que está no título e clique em Buscar anúncios.'); } });
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
  mensagem('SKU "' + v + '" preenchido em ' + alvo.length + ' anúncio(s). Confira, corrija o que precisar, digite TROCAR e clique em atualizar.', 'ok'); desenhar();
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
  const itens = LINHAS.filter(aparece).map(l => ({conta: l.conta, mlb: l.mlb, titulo: l.titulo, variacao: l.variacao, variacao_nome: l.variacao_nome || '', status: STATUS[l.status] || l.status || '', sku_atual: l.sku_atual, sku_novo: INDIVIDUAL() ? (PRONTA(l) ? l.novo.trim() : '') : (l.sku_novo || ''), link: l.link || ''}));
  if (!itens.length) { mensagem('Não há anúncios na lista para baixar.', 'erro'); return; }
  $('sExcel').disabled = true;
  try {
    const busca = REGRA ? ({igual: 'SKU igual a ', contem: 'SKU contém ', comeca: 'SKU começa com ', tit_contem: 'Título contém ', tit_exato: 'Título exato ', tit_comeca: 'Título começa com ', sem_contem: 'Sem SKU, título contém ', sem_exato: 'Sem SKU, título exato ', sem_comeca: 'Sem SKU, título começa com ', sem_todos: 'Todos sem SKU'}[REGRA.modo] || '') + (REGRA.modo === 'sem_todos' ? '' : REGRA.termo) : '';
    const filtro = $('sFiltro').value.trim(), semPal = $('sFiltroNao').value.trim();
    const r = await fetch('/api/skus/exportar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({itens, busca: busca + (filtro ? ' | filtro do título/variação: ' + filtro : '') + (semPal ? ' | sem as palavras: ' + semPal : '')})});
    if (r.status === 401) { location.href = '/login'; return; }
    if (!r.ok) { let d = null; try { d = await r.json(); } catch (e) {} throw new Error((d && d.error) || ('Erro ' + r.status)); }
    const blob = await r.blob(), a = el('a', {href: URL.createObjectURL(blob), download: 'anuncios.xlsx'});
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    mensagem(itens.length + ' anúncio(s) na planilha baixada.', 'ok');
  } catch (e) { mensagem('Não consegui baixar: ' + e.message, 'erro'); }
  $('sExcel').disabled = false;
}
$('sExcel').addEventListener('click', baixarExcel);

// botão ⧉ ao lado do MLB: copia o número (para colar no Mercado Livre, na planilha etc.)
async function copiarTexto(t){
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(t);
  const a = el('textarea', {}); a.value = t; a.style.position = 'fixed'; a.style.opacity = '0'; document.body.append(a); a.select();
  try { if (!document.execCommand('copy')) throw new Error('cópia recusada'); } finally { a.remove(); }
}
function botaoCopiar(texto){
  const b = el('button', {class: 'copiar', type: 'button', title: 'Copiar o MLB', 'aria-label': 'Copiar o MLB ' + texto}, '⧉');
  b.addEventListener('click', async e => { e.stopPropagation(); e.preventDefault(); try { await copiarTexto(String(texto)); b.textContent = '✓'; } catch (err) { b.textContent = '!'; } setTimeout(() => { b.textContent = '⧉'; }, 1200); });
  return b;
}
const celulaMlb = l => el('td', null, l.link ? el('a', {href: l.link, target: '_blank', rel: 'noopener'}, l.mlb) : l.mlb, botaoCopiar(l.mlb));

// ---------- pausar os anúncios ativos de um SKU (a busca só lê; pausar exige digitar PAUSAR) ----------
let P_LINHAS = [], P_BUSCANDO = false, P_PARAR = false, P_PAUSANDO = false;
let P_AUTO = false, P_ESPERANDO = 0, P_AUTO_ERRO = null;      // pausa automática durante a busca
const P_FILA = {}, P_RODANDO = {};
let P_TERMO = '', P_MOTIVO = null;                              // SKU da busca e motivo/previsão da pausa em andamento (vão para a planilha)
const P_FEITO = l => !!(l.resultado && l.resultado.status === 'OK');
function pMensagem(texto, tipo){ const m = $('pMsg'); m.textContent = texto || ''; m.hidden = !texto; m.className = 'aviso' + (tipo ? ' ' + tipo : ''); }
function pResumo(){
  const n = P_LINHAS.filter(l => l.marcado && !P_FEITO(l)).length, digitou = $('pConfirma').value.trim() === 'PAUSAR';
  $('pPausar').textContent = 'Pausar só os selecionados (' + n + ')';
  $('pPausar').disabled = P_BUSCANDO || P_PAUSANDO || P_AUTO || !n || !digitou;
  if (P_AUTO) {
    const fila = Object.values(P_FILA).reduce((t, f) => t + f.length, 0);
    $('pDica').textContent = 'Pausa automática ligada: ' + P_LINHAS.filter(P_FEITO).length + ' pausado(s) · ' + (fila + P_ESPERANDO) + ' na fila' + (P_ESPERANDO ? ' (' + P_ESPERANDO + ' esperando o Mercado Livre liberar)' : '') + ' · ' + P_LINHAS.filter(l => l.resultado && !P_FEITO(l) && !P_DEVAGAR(l)).length + ' não pausado(s).';
    return;
  }
  $('pDica').textContent = P_PAUSANDO ? 'Pausando no Mercado Livre… aguarde.' : P_BUSCANDO ? 'Aguarde a busca terminar para pausar.' : !n ? 'Nenhum anúncio marcado para pausar.' : !digitou ? 'Digite PAUSAR (só a palavra, em maiúsculas) para ligar o botão.' : 'Tudo certo: clique no botão para pausar no Mercado Livre.';
}
function pDesenhar(){
  $('pCorpo').replaceChildren(...P_LINHAS.map(l => {
    const cx = el('input', {type: 'checkbox', checked: l.marcado, disabled: P_PAUSANDO || P_FEITO(l)});
    cx.addEventListener('change', () => { l.marcado = cx.checked; pResumo(); });       // desmarcado sai da fila automática (ela só pausa os marcados)
    const sit = el('td', null, P_FEITO(l) ? 'Pausado' : 'Ativo');
    if (l.aviso) sit.append(el('div', {class: 'aviso-linha'}, l.aviso));
    if (l.resultado) sit.append(el('div', {class: P_FEITO(l) ? 'ok-linha' : 'aviso-linha'}, P_FEITO(l) ? 'Pausado e conferido ✔' : l.resultado.mensagem));
    return el('tr', null, el('td', null, cx), el('td', null, l.conta), celulaMlb(l), el('td', null, l.titulo), el('td', null, l.variacao_nome || '—'), el('td', null, l.sku_atual), sit);
  }));
  $('pResultado').hidden = !P_LINHAS.length;
  pResumo();
}
function pProgresso(texto, pct){
  $('pProg').hidden = texto === null; if (texto === null) return;
  $('pBarra').style.width = Math.max(2, Math.min(100, pct || 0)) + '%'; $('pTxt').textContent = texto;
}
function pMotivoLer(){
  const motivo = $('pMotivo').value, previsao = $('pPrevisao').value;
  if (!motivo) return {erro: 'Escolha o motivo da pausa: falta de estoque ou produto descontinuado.'};
  if (motivo === 'FALTA_ESTOQUE' && !/^\d{4}-\d{2}-\d{2}$/.test(previsao)) return {erro: 'Informe a data prevista para o SKU chegar (é nela que ele será reativado).'};
  return {motivo, previsao: motivo === 'FALTA_ESTOQUE' ? previsao : ''};
}
function pMotivoForm(){
  const desc = $('pMotivo').value === 'DESCONTINUADO';
  $('pPrevRot').hidden = desc; if (desc) $('pPrevisao').value = '';
}
const dataBr = iso => iso ? iso.split('-').reverse().join('/') : '';
// anota na aba "Pausas por SKU" (só a planilha; os anúncios já foram alterados). Falha aqui não desfaz nada: avisa para anotar à mão.
async function anotarPlanilha(idMsg, corpo, linhas){
  const m = $(idMsg);
  linhas.forEach(l => { l.anotado = true; });
  m.hidden = false; m.className = 'aviso'; m.textContent = 'Anotando na planilha…';
  try {
    const d = await comTentativas(() => json('/api/skus/pausas-registrar', corpo));
    m.className = 'aviso ok';
    m.textContent = 'Anotado na planilha (aba Pausas por SKU): SKU ' + corpo.sku + ' · ' + corpo.ok + (corpo.tipo === 'pausa' ? ' pausado(s)' : ' reativado(s)') + ' · ' + corpo.erros + ' com erro' +
      (corpo.tipo === 'pausa' ? ' · ' + (corpo.motivo === 'FALTA_ESTOQUE' ? 'falta de estoque, chega em ' + dataBr(corpo.previsao) : 'produto descontinuado') : ' · situação: ' + d.situacao) + '.';
    return true;
  } catch (e) {
    linhas.forEach(l => { l.anotado = false; });                  // na próxima rodada vão de novo
    m.className = 'aviso erro';
    m.textContent = 'Os anúncios já foram alterados no Mercado Livre, mas não consegui anotar na planilha: ' + e.message;
    return false;
  }
}
function pRegistrar(){
  if (!P_MOTIVO || !P_TERMO) return;
  const novos = P_LINHAS.filter(l => P_FEITO(l) && !l.anotado), erros = P_LINHAS.filter(l => l.resultado && !P_FEITO(l)).length;
  if (!novos.length && !erros) return;
  const contas = [...new Set(P_LINHAS.filter(l => l.resultado).map(l => l.conta))];
  anotarPlanilha('pPlanilha', {tipo: 'pausa', sku: P_TERMO, ok: novos.length, erros, contas, motivo: P_MOTIVO.motivo, previsao: P_MOTIVO.previsao}, novos);
}
async function pBuscar(auto){
  if (P_BUSCANDO || P_PAUSANDO || P_AUTO) return;
  const termo = $('pSku').value.trim();
  if (!termo) { pMensagem('Digite o SKU dos anúncios que serão pausados.', 'erro'); return; }
  if (auto === true) {
    const mot = pMotivoLer();
    if (mot.erro) { pMensagem(mot.erro, 'erro'); return; }
    const conf = prompt('Vou buscar e PAUSAR NO MERCADO LIVRE, um a um assim que aparecem, todos os anúncios ATIVOS com o SKU exato:\n\n' + termo +
      '\n\nMotivo: ' + (mot.motivo === 'FALTA_ESTOQUE' ? 'falta de estoque (chega em ' + dataBr(mot.previsao) + ')' : 'produto descontinuado') +
      '\n\nA pausa vale para o anúncio inteiro (inclusive variações com outro SKU). Para começar, digite PAUSAR:');
    if (conf === null) return;
    if (conf.trim() !== 'PAUSAR') { pMensagem('Pausa automática não ligada: é preciso digitar PAUSAR (em maiúsculas).', 'erro'); return; }
    P_AUTO = true; P_AUTO_ERRO = null; P_ESPERANDO = 0; P_MOTIVO = mot;
    for (const k of Object.keys(P_FILA)) delete P_FILA[k];
  }
  P_TERMO = termo; $('pPlanilha').hidden = true;
  const contas = $('pConta').value === 'AMBAS' ? ['ML19', 'ML44'] : [$('pConta').value], andamento = {};
  P_BUSCANDO = true; P_PARAR = false; P_LINHAS = []; $('pConfirma').value = ''; $('pBuscar').disabled = true; $('pBuscarAuto').disabled = true; $('pParar').hidden = false; pMensagem(''); pDesenhar();
  let falhou = null, parou = false;
  await Promise.all(contas.map(async conta => {
    let scroll = null; andamento[conta] = {lidos: 0, total: 0};
    try {
      for (;;) {
        if (P_PARAR) { parou = true; break; }
        const d = await comTentativas(() => json('/api/skus/buscar', {conta, scroll_id: scroll, modo: 'pausar_sku', termo}));
        scroll = d.scroll_id; andamento[conta].lidos += d.lidos; andamento[conta].total = d.total || andamento[conta].total;
        for (const a of d.achados) {
          const l = Object.assign({}, a, {conta, marcado: true, resultado: null, tentativas: 0});
          P_LINHAS.push(l);
          if (P_AUTO) pEnfileirar(l);
        }
        const lidos = Object.values(andamento).reduce((t, x) => t + x.lidos, 0), total = Object.values(andamento).reduce((t, x) => t + x.total, 0);
        pProgresso(lidos.toLocaleString('pt-BR') + (total ? ' de ' + total.toLocaleString('pt-BR') : '') + ' anúncios ativos lidos · ' + P_LINHAS.length + ' com este SKU', total ? lidos / total * 100 : 5);
        pDesenhar();
        if (d.concluido) break;
      }
    } catch (e) { falhou = falhou || e; P_PARAR = true; }
  }));
  P_BUSCANDO = false; pProgresso(null); pDesenhar();
  if (auto === true) {                                    // a pausa automática termina sozinha quando a fila esvaziar (ou mostra por que parou)
    if (falhou) P_AUTO_ERRO = P_AUTO_ERRO || ('a busca parou: ' + falhou.message + ' A lista pode estar incompleta.');
    pFimAuto(termo);
    return;
  }
  $('pBuscar').disabled = false; $('pBuscarAuto').disabled = false; $('pParar').hidden = true;
  if (falhou) pMensagem('A busca parou: ' + falhou.message + ' A lista pode estar incompleta.', 'erro');
  else if (parou) pMensagem('Busca interrompida. A lista mostra só o que foi lido até aqui.');
  else pMensagem(P_LINHAS.length ? P_LINHAS.length + ' anúncio(s) ativo(s) com o SKU ' + termo + '. Confira a lista antes de pausar.' : 'Nenhum anúncio ativo com o SKU ' + termo + '.', P_LINHAS.length ? 'ok' : '');
}
const P_DEVAGAR = l => !!(l.resultado && l.resultado.status === 'ERRO' && /devagar/.test(l.resultado.mensagem || ''));   // 429 do ML: nada foi alterado
const P_RODADAS = 3, P_ESPERA_S = 30;
async function pPausar(){
  const sel = P_LINHAS.filter(l => l.marcado && !P_FEITO(l));
  if (P_PAUSANDO || !sel.length || $('pConfirma').value.trim() !== 'PAUSAR') return;
  const mot = pMotivoLer();
  if (mot.erro) { pMensagem(mot.erro, 'erro'); return; }
  P_MOTIVO = mot;
  P_PAUSANDO = true; pMensagem(''); pDesenhar();
  let falhou = null, parar = false, alvo = sel;
  for (let rodada = 1; rodada <= P_RODADAS && alvo.length && !parar; rodada++) {
    if (rodada > 1) {                                     // o ML pediu para ir mais devagar: espera e tenta de novo SÓ esses (nada tinha sido alterado neles)
      for (let s = P_ESPERA_S; s > 0; s--) { pMensagem('O Mercado Livre pediu para ir mais devagar. Tentando de novo ' + alvo.length + ' anúncio(s) em ' + s + ' s…'); await new Promise(r => setTimeout(r, 1000)); }
      pMensagem('');
    }
    const porConta = {}; for (const l of alvo) (porConta[l.conta] = porConta[l.conta] || []).push(l);
    await Promise.all(Object.entries(porConta).map(async ([conta, ls]) => {
      try {
        for (let i = 0; i < ls.length && !parar; i += 10) {
          const lote = ls.slice(i, i + 10);
          const d = await json('/api/vendas/pausar', {conta, confirmacao: 'PAUSAR_ANUNCIOS', itens: lote.map(l => ({mlb: l.mlb, sku_visto: l.sku_atual}))});
          for (const r of d.resultados) {
            const l = lote.find(x => x.mlb === r.mlb);
            if (!l) continue;
            if (r.status === 'PULADO' && /Já estava pausado/.test(r.mensagem || '')) r.status = 'OK';     // já está como pedido
            l.resultado = r; if (r.status === 'OK') l.marcado = false;
          }
          pDesenhar();
        }
      } catch (e) { falhou = falhou || e; parar = true; }
    }));
    alvo = sel.filter(P_DEVAGAR);
  }
  P_PAUSANDO = false; $('pConfirma').value = ''; pDesenhar();
  const ok = sel.filter(P_FEITO).length, faltam = sel.length - ok;
  if (falhou) pMensagem('Parou no meio: ' + falhou.message + ' ' + ok + ' anúncio(s) pausado(s) até aqui. Os que faltam continuam marcados: digite PAUSAR e clique de novo.', 'erro');
  else pMensagem(ok + ' de ' + sel.length + ' anúncio(s) pausado(s) e conferido(s) no Mercado Livre.' + (faltam ? ' Os ' + faltam + ' que faltam continuam marcados (veja o motivo na linha): digite PAUSAR e clique de novo.' : ''), faltam ? 'erro' : 'ok');
  pRegistrar();
}
// ---------- pausa automática: cada anúncio entra na fila da sua conta assim que aparece; lotes de até 10, um por vez por conta ----------
function pEnfileirar(l){
  (P_FILA[l.conta] = P_FILA[l.conta] || []).push(l);
  pRodarFila(l.conta);
}
async function pRodarFila(conta){
  if (P_RODANDO[conta]) return;
  P_RODANDO[conta] = true;
  try {
    while (P_AUTO && (P_FILA[conta] || []).length) {
      const lote = P_FILA[conta].splice(0, 10).filter(l => l.marcado && !P_FEITO(l));
      if (!lote.length) continue;
      let d;
      try {
        d = await json('/api/vendas/pausar', {conta, confirmacao: 'PAUSAR_ANUNCIOS', itens: lote.map(l => ({mlb: l.mlb, sku_visto: l.sku_atual}))});
      } catch (e) {
        P_AUTO_ERRO = P_AUTO_ERRO || e.message; P_AUTO = false; P_PARAR = true;     // falha de conexão: para tudo (não repete às cegas)
        break;
      }
      for (const r of d.resultados) {
        const l = lote.find(x => x.mlb === r.mlb);
        if (!l) continue;
        if (r.status === 'PULADO' && /Já estava pausado/.test(r.mensagem || '')) r.status = 'OK';
        l.resultado = r; if (r.status === 'OK') l.marcado = false;
        if (P_DEVAGAR(l) && ++l.tentativas < P_RODADAS) {         // o ML pediu para ir mais devagar: nada foi alterado, tenta de novo daqui a pouco
          P_ESPERANDO++;
          setTimeout(() => { P_ESPERANDO--; if (P_AUTO) pEnfileirar(l); else pFimAuto(); }, P_ESPERA_S * 1000);
        }
      }
      pDesenhar();
    }
  } finally {
    P_RODANDO[conta] = false;
    pFimAuto();
  }
}
function pFimAuto(termo){
  if (termo) pFimAuto.termo = termo;
  const ocupado = Object.values(P_RODANDO).some(Boolean) || Object.values(P_FILA).some(f => f.length) || P_ESPERANDO > 0;
  if (P_BUSCANDO || (P_AUTO && ocupado)) { pDesenhar(); return; }
  if (!P_AUTO && !P_AUTO_ERRO && !P_PARAR && ocupado) return;
  if ($('pParar').hidden && !P_AUTO) return;             // já encerrado
  P_AUTO = false;
  for (const k of Object.keys(P_FILA)) P_FILA[k].length = 0;
  $('pBuscar').disabled = false; $('pBuscarAuto').disabled = false; $('pParar').hidden = true; $('pConfirma').value = '';
  pDesenhar();
  const ok = P_LINHAS.filter(P_FEITO).length, faltam = P_LINHAS.filter(l => l.marcado && !P_FEITO(l)).length;
  const base = ok + ' anúncio(s) pausado(s) e conferido(s) no Mercado Livre' + (pFimAuto.termo ? ' (SKU ' + pFimAuto.termo + ')' : '') + '.';
  const resto = faltam ? ' ' + faltam + ' continuam ativos e marcados (veja o motivo na linha): digite PAUSAR e clique em "Pausar só os selecionados".' : '';
  if (P_AUTO_ERRO) pMensagem('A pausa automática parou: ' + P_AUTO_ERRO + ' ' + base + resto, 'erro');
  else if (P_PARAR) pMensagem('Interrompido. ' + base + resto, faltam ? 'erro' : '');
  else pMensagem(P_LINHAS.length ? base + resto : 'Nenhum anúncio ativo com o SKU ' + (pFimAuto.termo || '') + '.', faltam ? 'erro' : (P_LINHAS.length ? 'ok' : ''));
  P_AUTO_ERRO = null;
  pRegistrar();
}
$('pBuscar').addEventListener('click', () => pBuscar(false));
$('pBuscarAuto').addEventListener('click', () => pBuscar(true));
$('pParar').addEventListener('click', () => { P_PARAR = true; if (P_AUTO) { P_AUTO = false; pFimAuto(); } });
$('pConfirma').addEventListener('input', pResumo);
$('pPausar').addEventListener('click', pPausar);
$('pMotivo').addEventListener('change', pMotivoForm);
pMotivoForm();

// ---------- reativar os anúncios pausados de um SKU (a busca só lê; reativar exige digitar REATIVAR) ----------
let R_LINHAS = [], R_BUSCANDO = false, R_PARAR = false, R_REATIVANDO = false, R_TERMO = '', R_PLAN_ABERTA = false;
const R_FEITO = l => !!(l.resultado && l.resultado.status === 'OK');
const R_DEVAGAR = l => !!(l.resultado && l.resultado.status === 'ERRO' && /devagar/.test(l.resultado.mensagem || ''));
function rMensagem(texto, tipo){ const m = $('rMsg'); m.textContent = texto || ''; m.hidden = !texto; m.className = 'aviso' + (tipo ? ' ' + tipo : ''); }
function rProgresso(texto, pct){
  $('rProg').hidden = texto === null; if (texto === null) return;
  $('rBarra').style.width = Math.max(2, Math.min(100, pct || 0)) + '%'; $('rTxt').textContent = texto;
}
function rResumo(){
  const n = R_LINHAS.filter(l => l.marcado && !R_FEITO(l)).length, digitou = $('rConfirma').value.trim() === 'REATIVAR';
  $('rReativar').textContent = 'Reativar só os selecionados (' + n + ')';
  $('rReativar').disabled = R_BUSCANDO || R_REATIVANDO || !n || !digitou;
  $('rDica').textContent = R_REATIVANDO ? 'Reativando no Mercado Livre… aguarde.' : R_BUSCANDO ? 'Aguarde a busca terminar para reativar.' : !R_LINHAS.length ? 'Primeiro digite o SKU e clique em "Buscar pausados com este SKU" (ou use "Reativar este SKU" na lista da planilha).' : !n ? 'Nenhum anúncio marcado para reativar.' : !digitou ? 'Digite REATIVAR (só a palavra, em maiúsculas) para ligar o botão.' : 'Tudo certo: clique no botão para reativar no Mercado Livre.';
}
function rDesenhar(){
  $('rCorpo').replaceChildren(...R_LINHAS.map(l => {
    const cx = el('input', {type: 'checkbox', checked: l.marcado, disabled: R_REATIVANDO || R_FEITO(l)});
    cx.addEventListener('change', () => { l.marcado = cx.checked; rResumo(); });
    const sit = el('td', null, R_FEITO(l) ? 'Ativo' : 'Pausado');
    if (l.aviso) sit.append(el('div', {class: 'aviso-linha'}, l.aviso));
    if (l.resultado) sit.append(el('div', {class: R_FEITO(l) ? 'ok-linha' : 'aviso-linha'}, R_FEITO(l) ? 'Reativado e conferido ✔' : l.resultado.mensagem));
    return el('tr', null, el('td', null, cx), el('td', null, l.conta), celulaMlb(l), el('td', null, l.titulo), el('td', null, l.variacao_nome || '—'), el('td', null, l.sku_atual),
      el('td', null, l.estoque === null || l.estoque === undefined ? '—' : String(l.estoque)), sit);
  }));
  $('rResultado').hidden = !R_LINHAS.length;
  rResumo();
}
async function rBuscar(){
  if (R_BUSCANDO || R_REATIVANDO) return;
  const termo = $('rSku').value.trim();
  if (!termo) { rMensagem('Digite o SKU dos anúncios que serão reativados.', 'erro'); return; }
  const contas = $('rConta').value === 'AMBAS' ? ['ML19', 'ML44'] : [$('rConta').value], andamento = {};
  R_BUSCANDO = true; R_PARAR = false; R_LINHAS = []; R_TERMO = termo; $('rConfirma').value = ''; $('rBuscar').disabled = true; $('rParar').hidden = false; $('rPlanilha').hidden = true; rMensagem(''); rDesenhar();
  let falhou = null, parou = false;
  await Promise.all(contas.map(async conta => {
    let scroll = null; andamento[conta] = {lidos: 0, total: 0};
    try {
      for (;;) {
        if (R_PARAR) { parou = true; break; }
        const d = await comTentativas(() => json('/api/skus/buscar', {conta, scroll_id: scroll, modo: 'reativar_sku', termo}));
        scroll = d.scroll_id; andamento[conta].lidos += d.lidos; andamento[conta].total = d.total || andamento[conta].total;
        for (const a of d.achados) R_LINHAS.push(Object.assign({}, a, {conta, marcado: a.estoque !== 0, resultado: null}));      // sem estoque o ML não reativa: vem desmarcado
        const lidos = Object.values(andamento).reduce((t, x) => t + x.lidos, 0), total = Object.values(andamento).reduce((t, x) => t + x.total, 0);
        rProgresso(lidos.toLocaleString('pt-BR') + (total ? ' de ' + total.toLocaleString('pt-BR') : '') + ' anúncios pausados lidos · ' + R_LINHAS.length + ' com este SKU', total ? lidos / total * 100 : 5);
        rDesenhar();
        if (d.concluido) break;
      }
    } catch (e) { falhou = falhou || e; R_PARAR = true; }
  }));
  R_BUSCANDO = false; rProgresso(null); rDesenhar();
  $('rBuscar').disabled = false; $('rParar').hidden = true;
  const semEstoque = R_LINHAS.filter(l => l.estoque === 0).length;
  if (falhou) rMensagem('A busca parou: ' + falhou.message + ' A lista pode estar incompleta.', 'erro');
  else if (parou) rMensagem('Busca interrompida. A lista mostra só o que foi lido até aqui.');
  else rMensagem(R_LINHAS.length ? R_LINHAS.length + ' anúncio(s) pausado(s) com o SKU ' + termo + '.' + (semEstoque ? ' ' + semEstoque + ' sem estoque vieram desmarcados (o Mercado Livre não reativa sem estoque).' : '') + ' Confira a lista antes de reativar.' : 'Nenhum anúncio pausado com o SKU ' + termo + '.', R_LINHAS.length ? 'ok' : '');
}
async function rReativar(){
  const sel = R_LINHAS.filter(l => l.marcado && !R_FEITO(l));
  if (R_REATIVANDO || !sel.length || $('rConfirma').value.trim() !== 'REATIVAR') return;
  R_REATIVANDO = true; rMensagem(''); rDesenhar();
  let falhou = null, parar = false, alvo = sel;
  for (let rodada = 1; rodada <= P_RODADAS && alvo.length && !parar; rodada++) {
    if (rodada > 1) {
      for (let s = P_ESPERA_S; s > 0; s--) { rMensagem('O Mercado Livre pediu para ir mais devagar. Tentando de novo ' + alvo.length + ' anúncio(s) em ' + s + ' s…'); await new Promise(r => setTimeout(r, 1000)); }
      rMensagem('');
    }
    const porConta = {}; for (const l of alvo) (porConta[l.conta] = porConta[l.conta] || []).push(l);
    await Promise.all(Object.entries(porConta).map(async ([conta, ls]) => {
      try {
        for (let i = 0; i < ls.length && !parar; i += 10) {
          const lote = ls.slice(i, i + 10);
          const d = await json('/api/skus/reativar', {conta, confirmacao: 'REATIVAR_ANUNCIOS', itens: lote.map(l => ({mlb: l.mlb, sku_visto: l.sku_atual}))});
          for (const r of d.resultados) {
            const l = lote.find(x => x.mlb === r.mlb);
            if (!l) continue;
            if (r.status === 'PULADO' && /Já estava ativo/.test(r.mensagem || '')) r.status = 'OK';
            l.resultado = r; if (r.status === 'OK') l.marcado = false;
          }
          rDesenhar();
        }
      } catch (e) { falhou = falhou || e; parar = true; }
    }));
    alvo = sel.filter(R_DEVAGAR);
  }
  R_REATIVANDO = false; $('rConfirma').value = ''; rDesenhar();
  const ok = sel.filter(R_FEITO).length, faltam = sel.length - ok;
  if (falhou) rMensagem('Parou no meio: ' + falhou.message + ' ' + ok + ' anúncio(s) reativado(s) até aqui. Os que faltam continuam marcados: digite REATIVAR e clique de novo.', 'erro');
  else rMensagem(ok + ' de ' + sel.length + ' anúncio(s) reativado(s) e conferido(s) no Mercado Livre.' + (faltam ? ' Os ' + faltam + ' que faltam continuam marcados (veja o motivo na linha): digite REATIVAR e clique de novo.' : ''), faltam ? 'erro' : 'ok');
  const novos = R_LINHAS.filter(l => R_FEITO(l) && !l.anotado), erros = R_LINHAS.filter(l => l.resultado && !R_FEITO(l)).length;
  if (novos.length || erros) {
    const anotou = await anotarPlanilha('rPlanilha', {tipo: 'reativacao', sku: R_TERMO, ok: novos.length, erros, contas: [...new Set(R_LINHAS.filter(l => l.resultado).map(l => l.conta))]}, novos);
    if (anotou && R_PLAN_ABERTA) rLerPlanilha();
  }
}
function dataDe(br){ const m = /^(\d{2})\/(\d{2})\/(\d{4})/.exec(br || ''); return m ? new Date(+m[3], +m[2] - 1, +m[1]) : null; }
async function rLerPlanilha(){
  $('rLerPlanilha').disabled = true; $('rPlanMsg').textContent = 'Lendo a planilha…';
  try {
    const d = await comTentativas(() => json('/api/skus/pausas-ler', {}));
    const hoje = new Date(); hoje.setHours(0, 0, 0, 0);
    R_PLAN_ABERTA = true;
    $('rPlanCorpo').replaceChildren(...d.pausas.map(x => {
      const prev = dataDe(x.previsao), chegou = prev && prev <= hoje;
      const b = el('button', {type: 'button'}, 'Reativar este SKU');
      b.addEventListener('click', () => {
        $('rSku').value = x.sku;
        const cs = (x.contas || '').split(/[,\s]+/).filter(Boolean);
        $('rConta').value = cs.length === 1 && ['ML19', 'ML44'].includes(cs[0]) ? cs[0] : 'AMBAS';
        $('rSku').scrollIntoView({block: 'center'}); rBuscar();
      });
      const tdPrev = el('td', null, x.previsao || '—');
      if (chegou) tdPrev.append(el('div', {class: 'ok-linha'}, 'chegou a data de reativar'));
      return el('tr', null, el('td', null, x.sku, botaoCopiar(x.sku)), el('td', null, x.motivo || '—', x.situacao && x.situacao !== 'Pausado' ? el('div', {class: 'aviso-linha'}, x.situacao) : null),
        tdPrev, el('td', null, x.pausados || '0'), el('td', null, x.data), el('td', null, x.contas || '—'), el('td', null, b));
    }));
    $('rPlanCaixa').hidden = !d.pausas.length;
    $('rPlanMsg').textContent = d.pausas.length ? d.pausas.length + ' SKU(s) pausado(s) na planilha (mais recentes primeiro).' : 'Nenhum SKU pausado anotado na planilha.';
  } catch (e) {
    $('rPlanMsg').textContent = 'Não consegui ler a planilha: ' + e.message;
  } finally { $('rLerPlanilha').disabled = false; }
}
$('rBuscar').addEventListener('click', rBuscar);
$('rParar').addEventListener('click', () => { R_PARAR = true; });
$('rConfirma').addEventListener('input', rResumo);
$('rReativar').addEventListener('click', rReativar);
$('rLerPlanilha').addEventListener('click', rLerPlanilha);
rResumo();
