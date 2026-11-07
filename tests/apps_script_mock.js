// Teste do Apps Script das etiquetas (apps_script/etiquetas.gs) com uma planilha simulada: nada de Google de verdade.
const fs = require('fs'), assert = require('assert');
class Faixa {
  constructor(sh, r, c, nr, nc) { this.sh = sh; this.r = r; this.c = c; this.nr = nr; this.nc = nc; }
  setValues(v) { for (let i = 0; i < v.length; i++) for (let j = 0; j < v[i].length; j++) { this.sh.d[(this.r + i) + ',' + (this.c + j)] = v[i][j]; } this.sh.max = Math.max(this.sh.max, this.r + v.length - 1); return this; }
  getValues() { const o = []; for (let i = 0; i < this.nr; i++) { const l = []; for (let j = 0; j < this.nc; j++) { const x = this.sh.d[(this.r + i) + ',' + (this.c + j)]; l.push(x === undefined ? '' : x); } o.push(l); } return o; }
  getDisplayValues() { return this.getValues().map(l => l.map(String)); }
  setNumberFormat(f) { if (f === '@') this.sh.texto.add(this.c); return this; }
  setFontWeight() { return this; } setBackground() { return this; } setFontColor() { return this; }
}
class Aba {
  constructor(nome) { this.nome = nome; this.d = {}; this.max = 0; this.charts = []; this.texto = new Set(); }
  getLastRow() { return this.max; }
  getRange(r, c, nr = 1, nc = 1) { return new Faixa(this, r, c, nr, nc); }
  clear() { this.d = {}; this.max = 0; return this; }
  setFrozenRows() {} getCharts() { return this.charts; } removeChart(c) { this.charts = this.charts.filter(x => x !== c); }
  insertChart(c) { this.charts.push(c); }
  newChart() { const o = { opts: {}, ranges: [] }; const b = { setChartType: t => { o.tipo = t; return b; }, addRange: r => { o.ranges.push(r); return b; }, setNumHeaders: () => b, setOption: (k, v) => { o.opts[k] = v; return b; }, setPosition: () => b, build: () => o }; return b; }
  linhas() { const o = []; for (let i = 1; i <= this.max; i++) { const l = []; for (let j = 1; j < 40; j++) { if (this.d[i + ',' + j] !== undefined) l[j - 1] = this.d[i + ',' + j]; } o.push(l); } return o; }
}
const abas = {};
global.wb_ = () => ({ getSheetByName: n => abas[n] || null, insertSheet: n => (abas[n] = new Aba(n)), deleteSheet: s => delete abas[s.nome] });
global.aba_ = (n, cab) => { if (!abas[n]) { abas[n] = new Aba(n); abas[n].getRange(1, 1, 1, cab.length).setValues([cab]); } return abas[n]; };
global.Utilities = { formatDate: (d, tz, f) => f.startsWith('yyyy') ? '2026-10-29' : '29/10/2026 16:00' };
global.LockService = { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) };
// ---- etiquetas (apps_script/etiquetas.gs): uma linha por pedido, marcar de novo atualiza
global.SpreadsheetApp = { flush() {} };
eval(fs.readFileSync(__dirname + '/../apps_script/etiquetas.gs', 'utf8') + '\nglobalThis.etiquetasRegistrar_ = etiquetasRegistrar_; globalThis.etiquetasLer_ = etiquetasLer_;');
let e1 = etiquetasRegistrar_({ registros: [{ conta: 'ML19', pedido: '111', envio_id: '9', sku: 'A_1', quantidade: 2, situacao: 'GERADA' }, { conta: 'ML44', pedido: '111', sku: 'B', quantidade: 1, situacao: 'GERADA' }, { conta: 'XX', pedido: '5', situacao: 'GERADA' }] });
assert.deepStrictEqual(e1, { gravados: 2, atualizados: 0 });
e1 = etiquetasRegistrar_({ registros: [{ conta: 'ML19', pedido: '111', sku: 'A_1', quantidade: 2, situacao: 'CANCELADA' }, { conta: 'ML19', pedido: '222', sku: 'C', quantidade: 3, situacao: 'GERADA' }] });
assert.deepStrictEqual(e1, { gravados: 1, atualizados: 1 });
assert.strictEqual(abas['Etiquetas'].linhas().length, 4);              // cabeçalho + 3 (sem repetir o 111 do ML19)
const lido = etiquetasLer_({ conta: 'ML19', pedidos: ['111', '222', '333'] }).etiquetas;
assert.deepStrictEqual(Object.keys(lido).sort(), ['111', '222']);
assert.strictEqual(lido['111'].situacao, 'CANCELADA');
assert.strictEqual(lido['222'].situacao, 'GERADA');
assert.deepStrictEqual(Object.keys(etiquetasLer_({ conta: 'ML44', pedidos: ['111'] }).etiquetas), ['111']);

console.log('apps script OK');
