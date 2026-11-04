// Teste do Apps Script apps_script/conhecimento_sku.gs com a planilha simulada (mesmas abas e colunas da planilha real).
const fs = require('fs'), assert = require('assert');
const aba = (nome, v) => ({ getName: () => nome, getDataRange: () => ({ getDisplayValues: () => v }) });
const abas = [
  aba('Regras Gerais', [['Prompt Geral', 'Regras Gerais', 'Respostas Padrão'],
    ['Você é um especialista técnico da Rede Mogilar.', 'Nunca invente informações.', 'FRETE\nO valor é calculado pelo Mercado Livre conforme o CEP.'],
    ['', 'Nunca altere medidas.', 'GARANTIA\nNenhum produto oferece garantia.']]),
  aba('Informações por sku', [['SKU', 'Familia', 'Regras Especiais'],
    ['VP4O_5m2', 'VP50', 'Este SKU possui 5 m². Comprimento linear: 5 ÷ 1,50 = 3,33 metros lineares.'],
    ['SEMFAM_1', '', 'Só regra.'], ['SEMBASE_1', 'Outra família', 'x']]),
  aba('Base de Conhecimento', [['Tópicos', 'VP50', 'Dreno 100mm'], ['O QUE É O PRODUTO?', 'Manta bidim.', 'Tubo corrugado.'], ['PARA QUE SERVE?', '', 'Drenagem.'], ['MODO DE UTILIZAÇÃO', 'Limpar a superfície.', '']]),
];
global.SpreadsheetApp = { getActiveSpreadsheet: () => ({ getSheets: () => abas }) };
global.ContentService = { MimeType: { JSON: 'json' }, createTextOutput: s => ({ s, setMimeType() { return this; } }) };
eval(fs.readFileSync(__dirname + '/../apps_script/conhecimento_sku.gs', 'utf8').replace("'TROQUE-POR-UMA-SENHA-LONGA';", "'senha-de-teste';")
  .replace("CHAVE_SITE_ === 'TROQUE-POR-UMA-SENHA-LONGA'", 'false') + '\nglobalThis.resp = conhecimentoSkuResposta_; globalThis.ler = conhecimentoSku_;');

const r = ler(['vp4o_5M2', 'SEMFAM_1', 'SEMBASE_1', 'NAOEXISTE']);   // sem diferença de maiúsculas
assert.deepStrictEqual(r.geral.regras, ['Nunca invente informações.', 'Nunca altere medidas.']);
assert.deepStrictEqual(r.geral.respostas.length, 2);
assert.strictEqual(r.geral.prompt, 'Você é um especialista técnico da Rede Mogilar.');
const a = r.skus['vp4o_5M2'];
assert.strictEqual(a.familia, 'VP50'); assert.ok(a.regras_especiais.includes('3,33 metros lineares'));
assert.deepStrictEqual(a.base, [{ topico: 'O QUE É O PRODUTO?', texto: 'Manta bidim.' }, { topico: 'MODO DE UTILIZAÇÃO', texto: 'Limpar a superfície.' }]);   // tópico vazio fica de fora
assert.strictEqual(r.skus['SEMFAM_1'].base_encontrada, false);
assert.strictEqual(r.skus['SEMBASE_1'].base_encontrada, false);
assert.deepStrictEqual(r.skus['NAOEXISTE'], { encontrado: false });
assert.strictEqual(JSON.parse(resp({ chave: 'errada', skus: [] }).s).ok, false);                      // chave errada não lê nada
assert.strictEqual(JSON.parse(resp({ chave: 'senha-de-teste', skus: [] }).s).ok, true);
assert.strictEqual(JSON.parse(resp({ chave: 'senha-de-teste', skus: 'x' }).s).ok, false);
abas.pop();
assert.strictEqual(JSON.parse(resp({ chave: 'senha-de-teste', skus: [] }).s).ok, false);              // aba faltando: erro claro
console.log('conhecimento apps script OK');
