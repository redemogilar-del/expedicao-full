"use strict";
const $ = id => document.getElementById(id);
const el = (tag, props, ...filhos) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) { if (k === 'class') e.className = v; else if (k.startsWith('aria-')) e.setAttribute(k, v); else e[k] = v; }
  for (const f of filhos) if (f !== null && f !== undefined) e.append(f instanceof Node ? f : String(f));
  return e;
};
const CONTAS = ['ML19', 'ML44'], UMA_HORA = 60, ESCALA = 90;
const CONFIRMACAO = $('pLista').dataset.confirmacao, MAX = Number($('pLista').dataset.max);
let PERGUNTAS = [], BUSCA_ATUAL = 0, EXPANDIDA = false;
const IA_LIGADA = $('pLista').dataset.ia === '1';
const SUGESTOES = new Map(), PEDINDO = new Set();
const RASCUNHOS = new Map(), ABERTAS = new Set(), CONFIRMANDO = new Set(), ENVIANDO = new Set(), RETORNOS = new Map();

function mensagem(texto){ const m = $('pMsg'); m.textContent = texto || ''; m.hidden = !texto; }
async function json(url, corpo){
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corpo)});
  if (r.status === 401) { location.href = '/login'; throw new Error('Sessão expirada'); }
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok || !d || d.ok === false) throw new Error((d && d.error) || ('Erro ' + r.status + '. O servidor pode estar acordando; tente de novo em 1 minuto.'));
  return d;
}
const brl = v => (v === null || v === undefined) ? '' : Number(v).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'});
function duracao(min){
  if (min === null || min === undefined) return 'Sem dados';
  if (min < 60) return Math.round(min) + ' min';
  if (min < 1440) { const h = Math.floor(min / 60), m = Math.round(min % 60); return h + ' h' + (m ? ' ' + m + ' min' : ''); }
  const d = Math.round(min / 1440); return d + (d === 1 ? ' dia' : ' dias');
}
const minutosDesde = iso => { const t = Date.parse(iso); return isNaN(t) ? null : Math.max(0, (Date.now() - t) / 60000); };
function idade(iso){
  const m = minutosDesde(iso);
  if (m === null) return '';
  if (m < 1) return 'Agora';
  if (m < 60) { const n = Math.floor(m); return 'Há ' + n + (n === 1 ? ' minuto' : ' minutos'); }
  if (m < 1440) { const n = Math.floor(m / 60); return 'Há ' + n + (n === 1 ? ' hora' : ' horas'); }
  const n = Math.floor(m / 1440); return 'Há ' + n + (n === 1 ? ' dia' : ' dias');
}

// ---------- prazo de resposta ----------
function linhaPrazo(p){
  const lento = p.minutos !== null && p.minutos > UMA_HORA, largura = p.minutos === null ? 0 : Math.max(1, Math.min(100, p.minutos / ESCALA * 100));
  const barra = el('i', {class: 'pg-barra ' + (lento ? 'pg-lento' : 'pg-rapido')}); barra.style.width = largura + '%';
  return el('div', {class: 'pg-linha', title: p.nome + ': ' + duracao(p.minutos)},
    el('span', {class: 'pg-nome'}, p.nome), el('span', {class: 'pg-trilha'}, barra), el('span', {class: 'pg-valor'}, duracao(p.minutos)));
}
function cartaoPrazo(d){
  const ruim = d.total !== null && d.total > UMA_HORA;
  const caixa = el('div', {class: 'pg-prazo'},
    el('h2', null, 'Prazo de resposta ', el('span', {class: 'tag'}, d.conta)),
    el('div', {class: 'pg-total'}, el('b', null, duracao(d.total)), ruim ? el('span', {class: 'tag v-prejuizo'}, 'Mais de 1 hora') : null),
    el('p', {class: 'suave pequeno'}, 'Média dos últimos 14 dias.' + (d.aumento ? ' O Mercado Livre estima até ' + d.aumento + '% mais vendas respondendo em até 1 hora.' : '')));
  if (d.periodos.length) {
    const escala = el('div', {class: 'pg-linha pg-eixo'}, el('span'), el('span', {class: 'pg-marcas'}, el('span', null, '0'), el('span', null, '30 min'), el('span', null, '1 h'), el('span', null, '+1h30')), el('span'));
    caixa.append(...d.periodos.map(linhaPrazo), escala);
  } else caixa.append(el('p', {class: 'suave pequeno'}, 'Esta conta ainda não tem dados de prazo.'));
  return caixa;
}
async function carregarPrazos(){
  const res = await Promise.allSettled(CONTAS.map(conta => json('/api/perguntas/prazo', {conta})));
  $('pPrazos').replaceChildren(...res.map((r, i) => r.status === 'fulfilled' ? cartaoPrazo(r.value)
    : el('div', {class: 'pg-prazo'}, el('h2', null, 'Prazo de resposta ', el('span', {class: 'tag'}, CONTAS[i])), el('p', {class: 'erro pequeno'}, r.reason.message))));
}

// ---------- lista ----------
async function carregar(){
  const pedido = ++BUSCA_ATUAL, dias = $('pDias').value ? Number($('pDias').value) : null, situacao = $('pSituacao').value;
  $('pAtualizar').disabled = true; $('pTotal').textContent = 'Buscando perguntas…'; mensagem('');
  const res = await Promise.allSettled(CONTAS.map(conta => json('/api/perguntas/listar', {conta, situacao, dias})));
  if (pedido !== BUSCA_ATUAL) return;
  const avisos = []; PERGUNTAS = [];
  res.forEach((r, i) => {
    if (r.status === 'rejected') { avisos.push(CONTAS[i] + ': ' + r.reason.message); return; }
    PERGUNTAS.push(...r.value.perguntas);
    if (r.value.incompleto) avisos.push(CONTAS[i] + ': mostrando só as ' + r.value.perguntas.length + ' mais recentes de ' + r.value.total + '. Escolha um período menor.');
    if (r.value.itens_falharam) avisos.push(CONTAS[i] + ': não consegui ler os dados de alguns anúncios. Clique em Atualizar.');
  });
  RETORNOS.clear(); CONFIRMANDO.clear(); mensagem(avisos.join(' '));
  $('pAtualizar').disabled = false; desenhar();
}
function filtradas(){
  const conta = $('pConta').value, termo = $('pBusca').value.trim().toLowerCase(), sinal = $('pOrdem').value === 'asc' ? 1 : -1;
  const lista = PERGUNTAS.filter(q => {
    if (conta && q.conta !== conta) return false;
    if (!termo) return true;
    return [q.texto, q.mlb, q.comprador, q.item && q.item.sku, q.item && q.item.titulo].some(v => String(v || '').toLowerCase().includes(termo));
  });
  return lista.sort((a, b) => sinal * ((Date.parse(a.data) || 0) - (Date.parse(b.data) || 0)));
}
function desenhar(){
  const lista = filtradas();
  $('pTotal').textContent = lista.length + (lista.length === 1 ? ' pergunta' : ' perguntas');
  if (!EXPANDIDA && lista.length && !lista.some(q => ABERTAS.has(q.id))) ABERTAS.add(lista[0].id);   // como no ML: a primeira já vem aberta
  $('pLista').replaceChildren(...lista.map(cartao));
  $('pVazio').hidden = lista.length > 0;
  $('pVazio').textContent = PERGUNTAS.length ? 'Nada encontrado com estes filtros.' : ($('pSituacao').value === 'a_responder' ? 'Nenhuma pergunta para responder.' : 'Nenhuma pergunta neste período.');
}
const ROTULOS = {ANSWERED: 'Respondida', UNANSWERED: 'A responder', CLOSED_UNANSWERED: 'Encerrada sem resposta', UNDER_REVIEW: 'Em análise', BANNED: 'Removida pelo Mercado Livre'};
function cartao(q){
  const aberta = EXPANDIDA || ABERTAS.has(q.id), it = q.item || {};
  const foto = it.thumb ? el('img', {class: 'pg-foto', src: it.thumb, alt: '', loading: 'lazy'}) : el('span', {class: 'pg-foto pg-sem-foto', 'aria-hidden': 'true'}, '?');
  const topo = el('button', {type: 'button', class: 'pg-topo', ariaExpanded: String(aberta), onclick: () => { if (ABERTAS.has(q.id)) ABERTAS.delete(q.id); else ABERTAS.add(q.id); desenhar(); }},
    foto,
    el('span', {class: 'pg-produto'}, el('small', null, 'SKU ' + (it.sku || '—') + ' · ' + q.mlb), el('span', {class: 'pg-titulo'}, it.titulo || 'Dados do anúncio indisponíveis')),
    el('span', {class: 'pg-dado'}, it.estoque === null || it.estoque === undefined ? '' : it.estoque + ' unidades'),
    el('b', {class: 'pg-dado'}, brl(it.preco)),
    el('span', {class: 'pg-dado suave'}, it.frete_gratis ? 'Frete grátis' : ''),
    el('span', {class: 'tag'}, q.conta),
    el('span', {class: 'pg-seta', 'aria-hidden': 'true'}, aberta ? '⌃' : '⌄'));
  const caixa = el('article', {class: 'pg-cartao'}, topo);
  if (!aberta) { caixa.append(el('p', {class: 'pg-previa'}, el('i', {class: 'pg-marca'}), q.texto || 'Texto removido pelo Mercado Livre.', el('span', {class: 'suave pequeno'}, idade(q.data)))); return caixa; }
  caixa.append(corpo(q)); return caixa;
}
function corpo(q){
  const quem = q.comprador || (q.comprador_id ? 'Cliente nº ' + q.comprador_id : 'Cliente');
  const atrasa = q.situacao === 'UNANSWERED' && minutosDesde(q.data) > UMA_HORA;
  const c = el('div', {class: 'pg-corpo'},
    el('p', {class: 'suave pequeno pg-quem'}, quem, (q.item && q.item.link) ? el('a', {href: q.item.link, target: '_blank', rel: 'noopener noreferrer'}, 'Ver anúncio') : null),
    el('p', {class: 'pg-pergunta'}, el('i', {class: 'pg-marca'}), el('b', null, q.texto || 'Texto removido pelo Mercado Livre.'), el('span', {class: 'suave pequeno'}, idade(q.data)), atrasa ? el('span', {class: 'pg-atraso pequeno'}, '! Afeta seu tempo de resposta') : null));
  if (q.situacao === 'UNANSWERED') c.append(...formulario(q));
  else {
    if (q.resposta) c.append(el('div', {class: 'pg-resposta'}, el('small', {class: 'suave'}, 'Resposta' + (q.resposta.data ? ' · ' + new Date(q.resposta.data).toLocaleString('pt-BR') : '')), el('p', null, q.resposta.texto || 'Texto removido pelo Mercado Livre.')));
    else c.append(el('p', {class: 'suave pequeno'}, ROTULOS[q.situacao] || q.situacao));
  }
  const ret = RETORNOS.get(q.id);
  if (ret) c.append(el('p', {class: 'aviso' + (ret.status === 'OK' ? ' ok' : ret.status === 'ERRO' ? ' erro' : ''), role: 'status'}, ret.mensagem));
  return c;
}
function formulario(q){
  const envia = ENVIANDO.has(q.id), confirma = CONFIRMANDO.has(q.id);
  const texto = el('textarea', {class: 'pg-texto', rows: 3, maxLength: MAX, placeholder: 'Você pode enviar apenas 1 resposta. Não inclua dados de contato.', value: RASCUNHOS.get(q.id) || '', disabled: envia, readOnly: confirma, ariaLabel: 'Resposta para a pergunta'});
  const conta = el('span', {class: 'suave pequeno'}, texto.value.length + ' / ' + MAX);
  const botao = el('button', {type: 'button', class: 'primario', disabled: !texto.value.trim() || envia, onclick: () => { CONFIRMANDO.add(q.id); RETORNOS.delete(q.id); desenhar(); }}, 'Responder');
  texto.oninput = () => { RASCUNHOS.set(q.id, texto.value); conta.textContent = texto.value.length + ' / ' + MAX; botao.disabled = !texto.value.trim(); };
  const acoes = confirma
    ? el('div', {class: 'pg-acoes pg-confirma'}, el('span', null, 'Enviar esta resposta? Depois não dá para enviar outra.'),
        el('button', {type: 'button', class: 'primario', disabled: envia, onclick: () => enviar(q)}, envia ? 'Enviando…' : 'Confirmar envio'),
        el('button', {type: 'button', disabled: envia, onclick: () => { CONFIRMANDO.delete(q.id); desenhar(); }}, 'Voltar'))
    : el('div', {class: 'pg-acoes'}, conta, botao);
  return [texto, ...caixaSugestao(q, texto, envia || confirma), acoes];
}
// ---------- resposta sugerida pela IA (só preenche a caixa; quem envia é você) ----------
function caixaSugestao(q, texto, travado){
  const s = SUGESTOES.get(q.id), pedindo = PEDINDO.has(q.id);
  const botao = el('button', {type: 'button', class: 'pg-chip', disabled: travado || pedindo || !IA_LIGADA,
    title: IA_LIGADA ? 'A IA lê o anúncio e escreve um rascunho. Nada é enviado ao Mercado Livre.' : 'A IA ainda não foi ligada: falta a chave EXP_ANTHROPIC_KEY no Render.',
    onclick: () => sugerir(q)}, pedindo ? 'Pensando…' : (s ? '✦ Sugerir de novo' : '✦ Ver resposta sugerida'));
  const saida = [el('div', {class: 'pg-chips'}, botao, IA_LIGADA ? null : el('span', {class: 'suave pequeno'}, 'A IA ainda não foi ligada (falta a chave no Render).'))];
  if (s && s.erro) saida.push(el('p', {class: 'aviso erro', role: 'status'}, s.erro));
  else if (s) {
    const caixa = el('div', {class: 'pg-sugestao', role: 'status'}, el('small', {class: 'suave'}, '✦ Rascunho da IA — leia e confira antes de enviar'));
    if (s.resposta) {
      caixa.append(el('p', null, s.resposta), el('div', {class: 'pg-acoes'}, el('span'), el('button', {type: 'button', disabled: travado, onclick: () => {
        RASCUNHOS.set(q.id, s.resposta); SUGESTOES.delete(q.id); desenhar(); }}, 'Usar esta resposta')));
    }
    if (s.falta) caixa.append(el('p', {class: 'pg-falta pequeno'}, (s.resposta ? 'Confira: ' : 'A IA não tem esta informação no anúncio. ') + s.falta));
    saida.push(caixa);
  }
  return saida;
}
async function sugerir(q){
  if (PEDINDO.has(q.id)) return;
  PEDINDO.add(q.id); SUGESTOES.delete(q.id); desenhar();
  try { SUGESTOES.set(q.id, await json('/api/perguntas/sugerir', {conta: q.conta, pergunta: q.id})); }
  catch (e) { SUGESTOES.set(q.id, {erro: e.message}); }
  PEDINDO.delete(q.id); desenhar();
}
async function enviar(q){
  if (ENVIANDO.has(q.id)) return;
  ENVIANDO.add(q.id); desenhar();
  try {
    const r = await json('/api/perguntas/responder', {conta: q.conta, pergunta: q.id, texto: RASCUNHOS.get(q.id) || '', confirmacao: CONFIRMACAO});
    RETORNOS.set(q.id, r);
    if (r.resposta) { q.resposta = r.resposta; q.situacao = 'ANSWERED'; }
    if (r.status === 'OK') RASCUNHOS.delete(q.id);
  } catch (e) {
    RETORNOS.set(q.id, {status: 'ERRO', mensagem: e.message + ' Atualize a lista antes de tentar de novo, para não responder duas vezes.'});
  }
  ENVIANDO.delete(q.id); CONFIRMANDO.delete(q.id); desenhar();
}
function visao(expandida){
  EXPANDIDA = expandida; $('pExpandida').classList.toggle('on', expandida); $('pColapsada').classList.toggle('on', !expandida); desenhar();
}
$('pExpandida').onclick = () => visao(true);
$('pColapsada').onclick = () => visao(false);
$('pAtualizar').onclick = carregar;
$('pDias').onchange = carregar;
$('pSituacao').onchange = carregar;
$('pConta').onchange = desenhar;
$('pOrdem').onchange = desenhar;
$('pBusca').oninput = desenhar;
carregarPrazos(); carregar();
