// Teste do Apps Script apps_script/fichas_sku.gs com planilha simulada.
const fs = require('fs'), assert = require('assert');
const dados = {}; let max = 0, formato = null;
const aba = {
  getLastRow: () => max, setFrozenRows() {},
  getRange: (r, c, nr = 1, nc = 1) => ({
    setValues(v) { v.forEach((l, i) => l.forEach((x, j) => { dados[(r + i) + ',' + (c + j)] = x; })); max = Math.max(max, r + v.length - 1); return this; },
    setNumberFormat(f) { formato = f; return this; },
    getDisplayValues() { const o = []; for (let i = 0; i < nr; i++) { const l = []; for (let j = 0; j < nc; j++) { const x = dados[(r + i) + ',' + (c + j)]; l.push(x === undefined ? '' : String(x)); } o.push(l); } return o; },
  }),
};
let criada = false;
global.wb_ = () => ({ getSheetByName: () => (criada ? aba : null), insertSheet: () => { criada = true; return aba; } });
global.Utilities = { formatDate: () => '04/11/2026 10:00' };
global.LockService = { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) };
eval(fs.readFileSync(__dirname + '/../apps_script/fichas_sku.gs', 'utf8') + '\nglobalThis.ler = fichasSkuLer_; globalThis.salvar = fichasSkuSalvar_;');

assert.deepStrictEqual(ler(['GEO2']).fichas, {});                       // cria a aba, sem fichas
assert.strictEqual(salvar('GEO2_160m2', 'Gramatura 150 g/m²').criada, true);
assert.strictEqual(formato, '@');                                       // texto puro
assert.strictEqual(ler(['geo2_160M2']).fichas['geo2_160m2'].texto, 'Gramatura 150 g/m²');   // sem diferença de maiúsculas
assert.strictEqual(salvar('GEO2_160M2', '=SOMA(1)').criada, false);     // mesma linha, não duplica
assert.strictEqual(max, 2);
assert.strictEqual(ler(['GEO2_160m2']).fichas['geo2_160m2'].texto, '=SOMA(1)');
assert.deepStrictEqual(ler(['OUTRO']).fichas, {});
salvar('GEO2_160m2', '');                                               // vazio apaga as informações
assert.deepStrictEqual(ler(['GEO2_160m2']).fichas, {});
assert.throws(() => salvar('a\nb', 'x')); assert.throws(() => salvar('A', 'x'.repeat(2001))); assert.throws(() => ler('A'));
console.log('fichas apps script OK');
