/**
 * TOKEN DA SHOPEE — Apps Script próprio (projeto separado da planilha), publicado como App da Web.
 *
 * Guarda o acesso da loja (access_token de 4 h e refresh_token de 30 dias, que muda a cada renovação) nas
 * Propriedades do script e entrega ao site da expedição um access_token válido. O site não grava nada.
 *
 * Configurar (uma vez), em Configurações do projeto > Propriedades do script:
 *   PARTNER_ID   = Partner ID do app no Shopee Open Platform (só números)
 *   PARTNER_KEY  = Partner Key do app (segredo; nunca vai para o site nem para a tela)
 *   CHAVE_SITE   = uma senha qualquer, a mesma de EXP_SHOPEE_TOKEN_KEY no Render
 * Depois: Implantar > Nova implantação > App da Web (Executar como: Eu; Quem pode acessar: Qualquer pessoa),
 * rodar a função linkDeAutorizacao, abrir o link do registro de execução e autorizar a loja.
 * Por último, rodar instalarRenovacao (renova a cada 3 h, para o refresh_token nunca vencer).
 */
var SHOPEE_HOST = 'https://openplatform.shopee.com.br';

function props_() { return PropertiesService.getScriptProperties(); }

function hex_(bytes) {
  return bytes.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
}

function assinar_(base) {
  return hex_(Utilities.computeHmacSha256Signature(base, String(props_().getProperty('PARTNER_KEY') || '')));
}

function config_() {
  const p = props_(), id = String(p.getProperty('PARTNER_ID') || '').trim();
  if (!/^\d+$/.test(id) || !p.getProperty('PARTNER_KEY')) throw Error('Falta PARTNER_ID ou PARTNER_KEY nas Propriedades do script.');
  return { id: id };
}

// Chamada pública da Shopee (token): sign = HMAC(partner_id + caminho + timestamp).
function publica_(caminho, corpo) {
  const id = config_().id, ts = Math.floor(Date.now() / 1000);
  const url = SHOPEE_HOST + caminho + '?partner_id=' + id + '&timestamp=' + ts + '&sign=' + assinar_(id + caminho + ts);
  const r = UrlFetchApp.fetch(url, { method: 'post', contentType: 'application/json', payload: JSON.stringify(corpo), muteHttpExceptions: true });
  let d;
  try { d = JSON.parse(r.getContentText()); } catch (e) { throw Error('Resposta inválida da Shopee (HTTP ' + r.getResponseCode() + ').'); }
  if (d.error) throw Error('A Shopee recusou (' + d.error + '): ' + String(d.message || '').slice(0, 200));
  if (!d.access_token || !d.refresh_token) throw Error('A Shopee não devolveu o token.');
  return d;
}

function guardar_(d, shopId) {
  props_().setProperties({
    SHOP_ID: String(shopId), ACCESS_TOKEN: String(d.access_token), REFRESH_TOKEN: String(d.refresh_token),
    EXPIRA: String(Date.now() + (Number(d.expire_in) || 14400) * 1000), RENOVADO: new Date().toISOString()
  });
}

// Troca o código da autorização (vem no endereço de volta, uma vez só) pelo token.
function trocarCodigo_(code, shopId) {
  const id = config_().id;
  guardar_(publica_('/api/v2/auth/token/get', { code: String(code), shop_id: Number(shopId), partner_id: Number(id) }), shopId);
}

// Renova com o refresh_token (que muda a cada renovação; o trava impede duas renovações ao mesmo tempo).
function renovar_(forcar) {
  const trava = LockService.getScriptLock();
  trava.waitLock(30000);
  try {
    const p = props_(), shop = p.getProperty('SHOP_ID'), refresh = p.getProperty('REFRESH_TOKEN');
    if (!shop || !refresh) throw Error('A loja ainda não foi autorizada. Rode linkDeAutorizacao e autorize.');
    if (!forcar && Number(p.getProperty('EXPIRA') || 0) - Date.now() > 20 * 60 * 1000) return;   // outro pedido já renovou
    guardar_(publica_('/api/v2/auth/access_token/get', { refresh_token: refresh, shop_id: Number(shop), partner_id: Number(config_().id) }), shop);
  } finally {
    trava.releaseLock();
  }
}

function tokenValido_() {
  const p = props_();
  if (!p.getProperty('ACCESS_TOKEN') || Number(p.getProperty('EXPIRA') || 0) - Date.now() < 20 * 60 * 1000) renovar_(false);
  return { access_token: p.getProperty('ACCESS_TOKEN'), shop_id: p.getProperty('SHOP_ID'), expira_em_s: Math.floor((Number(p.getProperty('EXPIRA')) - Date.now()) / 1000) };
}

function resposta_(o) { return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON); }

// O site pede o token: POST {chave, acao: 'token'}.
function doPost(e) {
  try {
    const b = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    const chave = String(props_().getProperty('CHAVE_SITE') || '');
    if (!chave || String(b.chave || '') !== chave) return resposta_({ ok: false, erro: 'Chave inválida.' });
    if (b.acao !== 'token') return resposta_({ ok: false, erro: 'Ação desconhecida.' });
    const t = tokenValido_();
    return resposta_({ ok: true, access_token: t.access_token, shop_id: t.shop_id, expira_em_s: t.expira_em_s });
  } catch (err) {
    return resposta_({ ok: false, erro: String(err && err.message || err).slice(0, 300) });
  }
}

// A Shopee volta aqui depois que você autoriza a loja: ?code=...&shop_id=...
function doGet(e) {
  const q = (e && e.parameter) || {};
  try {
    if (q.code && /^\d+$/.test(String(q.shop_id || ''))) {
      trocarCodigo_(q.code, q.shop_id);
      return HtmlService.createHtmlOutput('<p>Loja Shopee ' + Number(q.shop_id) + ' autorizada. Pode fechar esta página.</p>');
    }
    return HtmlService.createHtmlOutput('<p>Token da Shopee: ' + (props_().getProperty('SHOP_ID') ? 'loja autorizada.' : 'loja ainda não autorizada.') + '</p>');
  } catch (err) {
    return HtmlService.createHtmlOutput('<p>Não deu certo: ' + String(err && err.message || err).replace(/[<>&]/g, '') + '</p>');
  }
}

// Rode no editor: o registro de execução mostra o link para autorizar a loja (vale 5 minutos).
function linkDeAutorizacao() {
  const id = config_().id, caminho = '/api/v2/shop/auth_partner', ts = Math.floor(Date.now() / 1000);
  const volta = ScriptApp.getService().getUrl();
  if (!volta) throw Error('Publique antes como App da Web (Implantar > Nova implantação).');
  const link = SHOPEE_HOST + caminho + '?partner_id=' + id + '&timestamp=' + ts + '&sign=' + assinar_(id + caminho + ts) + '&redirect=' + encodeURIComponent(volta);
  Logger.log(link);
  return link;
}

// Rode uma vez: renova a cada 3 horas (o refresh_token vence em 30 dias se não for usado).
function instalarRenovacao() {
  ScriptApp.getProjectTriggers().filter(function (t) { return t.getHandlerFunction() === 'renovarAgendado'; }).forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('renovarAgendado').timeBased().everyHours(3).create();
}

function renovarAgendado() { renovar_(true); }
