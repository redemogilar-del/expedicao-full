// Teste do Apps Script do token da Shopee (apps_script/shopee_token.gs), sem Google nem Shopee de verdade.
const fs = require('fs'), assert = require('assert'), crypto = require('crypto');
const P = { PARTNER_ID: '123', PARTNER_KEY: 'segredo', CHAVE_SITE: 'senha' };
global.PropertiesService = { getScriptProperties: () => ({ getProperty: k => (k in P ? P[k] : null), setProperties: o => Object.assign(P, o) }) };
global.Utilities = { computeHmacSha256Signature: (v, k) => Array.from(crypto.createHmac('sha256', k).update(v).digest()).map(b => (b > 127 ? b - 256 : b)) };
global.LockService = { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) };
global.ContentService = { MimeType: { JSON: 'json' }, createTextOutput: t => ({ texto: t, setMimeType() { return this; } }) };
global.HtmlService = { createHtmlOutput: h => ({ html: h }) };
global.ScriptApp = { getService: () => ({ getUrl: () => 'https://script.google.com/macros/s/X/exec' }) };
global.Logger = { log() {} };
const chamadas = [];
let resposta = { access_token: 'AT1', refresh_token: 'RT1', expire_in: 14400 };
global.UrlFetchApp = { fetch: (url, op) => { chamadas.push({ url, corpo: JSON.parse(op.payload) }); return { getContentText: () => JSON.stringify(resposta), getResponseCode: () => 200 }; } };
eval(fs.readFileSync(__dirname + '/../apps_script/shopee_token.gs', 'utf8') +
  '\nglobalThis.doGet = doGet; globalThis.doPost = doPost; globalThis.linkDeAutorizacao = linkDeAutorizacao; globalThis.renovarAgendado = renovarAgendado;');

const sign = (base) => crypto.createHmac('sha256', 'segredo').update(base).digest('hex');
const post = b => JSON.parse(doPost({ postData: { contents: JSON.stringify(b) } }).texto);

// sem autorizar: erro claro
assert.strictEqual(post({ chave: 'senha', acao: 'token' }).ok, false);
assert.strictEqual(post({ chave: 'errada', acao: 'token' }).erro, 'Chave inválida.');
// link de autorização assinado
const link = linkDeAutorizacao();
const ts = link.match(/timestamp=(\d+)/)[1];
assert.ok(link.startsWith('https://openplatform.shopee.com.br/api/v2/shop/auth_partner?partner_id=123&timestamp='));
assert.ok(link.includes('sign=' + sign('123/api/v2/shop/auth_partner' + ts)));
assert.ok(link.includes('redirect=' + encodeURIComponent('https://script.google.com/macros/s/X/exec')));
// a Shopee volta com o código: troca e guarda
assert.ok(doGet({ parameter: { code: 'C1', shop_id: '999' } }).html.includes('autorizada'));
assert.strictEqual(chamadas[0].url.split('?')[0], 'https://openplatform.shopee.com.br/api/v2/auth/token/get');
assert.deepStrictEqual(chamadas[0].corpo, { code: 'C1', shop_id: 999, partner_id: 123 });
assert.ok(chamadas[0].url.includes('sign=' + sign('123/api/v2/auth/token/get' + chamadas[0].url.match(/timestamp=(\d+)/)[1])));
assert.strictEqual(P.REFRESH_TOKEN, 'RT1');
// o site pega o token (ainda válido: não renova)
let t = post({ chave: 'senha', acao: 'token' });
assert.deepStrictEqual([t.ok, t.access_token, t.shop_id], [true, 'AT1', '999']);
assert.strictEqual(chamadas.length, 1);
// perto de vencer: renova com o refresh_token e guarda o NOVO refresh_token
P.EXPIRA = String(Date.now() + 60 * 1000);
resposta = { access_token: 'AT2', refresh_token: 'RT2', expire_in: 14400 };
t = post({ chave: 'senha', acao: 'token' });
assert.strictEqual(t.access_token, 'AT2');
assert.strictEqual(chamadas[1].url.split('?')[0], 'https://openplatform.shopee.com.br/api/v2/auth/access_token/get');
assert.deepStrictEqual(chamadas[1].corpo, { refresh_token: 'RT1', shop_id: 999, partner_id: 123 });
assert.strictEqual(P.REFRESH_TOKEN, 'RT2');
// renovação agendada usa o último refresh_token
resposta = { access_token: 'AT3', refresh_token: 'RT3', expire_in: 14400 };
renovarAgendado();
assert.deepStrictEqual([chamadas[2].corpo.refresh_token, P.ACCESS_TOKEN], ['RT2', 'AT3']);
// recusa da Shopee aparece para o site, sem apagar o token guardado
P.EXPIRA = '0'; resposta = { error: 'error_auth', message: 'refresh expirado' };
t = post({ chave: 'senha', acao: 'token' });
assert.strictEqual(t.ok, false); assert.ok(t.erro.includes('error_auth'));
assert.strictEqual(P.REFRESH_TOKEN, 'RT3');
console.log('shopee token OK');
