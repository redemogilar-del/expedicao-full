// Teste do Apps Script (apps_script/relatorio_vendas.gs) com uma planilha simulada: nada de Google de verdade.
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
global.Charts = { ChartType: { LINE: 'LINE' } };
eval(fs.readFileSync(__dirname + '/../apps_script/relatorio_vendas.gs', 'utf8') + '\nglobalThis.vendasRelatorio_ = vendasRelatorio_; globalThis.vendasPrecos_ = vendasPrecos_;');

const cab = ['Chave', 'Data', 'Classe', 'Conta', 'Pedido', 'SKU', 'MLB', 'Status do envio', 'Produto', 'Qtd', 'Venda (R$)', 'Tarifa ML (R$)', 'Frete vendedor (R$)', 'Imposto (R$)', 'Custo total (R$)', 'Lucro (R$)', 'Margem real (%)', 'Margem mínima (%)', 'Prejuízo / deixou de ganhar (R$)', 'Tipo de envio', 'Atualizado em'];
const lin = (conta, pedido, data, classe, valor) => [conta + '|' + pedido + '|MLB1|A', data, classe, conta, pedido, 'A', 'MLB1', 'Enviado', 'Prod', 1, 100, 10, 5, 8, 50, 20, 10, 20, valor, 'A enviar', '29/10/2026 16:00'];
const pedido = (base) => ({ dia: '2026-10-29', periodo: 'x', cab: ['a'], linhas: [['1']], nao_enviar: [], resumo: {}, parametros: {}, cab_base: cab, base });

// 1ª vez: 2 vendas ML19 (uma prejuízo, uma abaixo) e 1 do ML44
let r = vendasRelatorio_(pedido([lin('ML19', '1', '2026-10-28', 'Prejuízo', 30), lin('ML19', '2', '2026-10-29', 'Abaixo da margem', 12), lin('ML44', '9', '2026-10-29', 'Dentro da margem', 0)]));
assert(r.ok);
assert.strictEqual(abas['Base ML19'].linhas().length, 3);            // cabeçalho + 2
assert.strictEqual(abas['Base ML44'].linhas().length, 2);
// 2ª vez: a venda 1 volta (já existia) com valor corrigido, e entra a 3
vendasRelatorio_(pedido([lin('ML19', '1', '2026-10-28', 'Prejuízo', 35), lin('ML19', '3', '2026-10-28', 'Prejuízo', 5)]));
let base = abas['Base ML19'].linhas();
assert.strictEqual(base.length, 4, 'não pode repetir a venda 1');
assert.strictEqual(base.filter(l => l[0] === 'ML19|1|MLB1|A').length, 1);
assert.strictEqual(base.find(l => l[0] === 'ML19|1|MLB1|A')[18], 35);   // atualizou
// salvar a mesma coisa de novo não muda nada
vendasRelatorio_(pedido([lin('ML19', '1', '2026-10-28', 'Prejuízo', 35)]));
assert.strictEqual(abas['Base ML19'].linhas().length, 4);
// derivadas
const prej = abas['Prejuízos ML19'].linhas(), abx = abas['Abaixo da margem ML19'].linhas();
assert.deepStrictEqual(prej.slice(1).map(l => l[3]), ['1', '3']);      // pedido (sem a coluna Chave), maior prejuízo primeiro
assert.deepStrictEqual(abx.slice(1).map(l => l[3]), ['2']);
assert(!abas['Prejuízos ML44'].linhas().slice(1).length);
// desempenho + gráficos
const d = abas['Desempenho ML19'].linhas();
assert.deepStrictEqual(d[1], ['2026-10-28', 40, 0, 2, 2, 0, 0]);
assert.deepStrictEqual(d[2], ['2026-10-29', 0, 12, 1, 0, 1, 0]);
assert.strictEqual(abas['Desempenho ML19'].charts.length, 2);
assert(abas['Desempenho ML19'].charts[0].opts.title.includes('ML19'));
// preços atualizados: não repete
const it = [{ mlb: 'MLB1', sku: 'A', produto: 'P', preco_anterior: 100, preco_novo: 110, variacao_pct: 10, pedidos: ['1', '2'] }];
assert.deepStrictEqual(Object.assign({}, vendasPrecos_({ conta: 'ML19', itens: it })), { ok: true, aba: 'Preços atualizados ML19', novos: 1, repetidos: 0 });
assert.strictEqual(vendasPrecos_({ conta: 'ML19', itens: it }).repetidos, 1);
assert.strictEqual(abas['Preços atualizados ML19'].linhas().length, 2);
assert.throws(() => vendasPrecos_({ conta: 'XX', itens: it }));
console.log('apps script OK');
