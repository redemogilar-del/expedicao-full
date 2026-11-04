// Acréscimo ao Apps Script "ML HUB MOGILAR — GOOGLE SHEETS" (V7).
// Guarda as "fichas por SKU" da aba Perguntas do painel Expedição Mogilar (aba "Fichas SKU":
// A = SKU, B = Informações, C = Atualizado em). A IA usa essas informações para sugerir respostas.
//
// 1) No switch (b.acao) do doPost, acrescente:
//
//      case 'fichas_sku_ler':
//        r = fichasSkuLer_(b.skus);
//        break;
//      case 'fichas_sku_salvar':
//        r = fichasSkuSalvar_(b.sku, b.texto);
//        break;
//
// 2) Cole as funções abaixo no final do Código.gs.
// 3) Implantar > Gerenciar implantações > lápis > Nova versão > Implantar (a URL não muda).

function fichasSkuAba_() {
  let sh = wb_().getSheetByName('Fichas SKU');
  if (!sh) {
    sh = wb_().insertSheet('Fichas SKU');
    sh.getRange(1, 1, 1, 3).setValues([['SKU', 'Informações', 'Atualizado em']]);
    sh.setFrozenRows(1);
  }
  return sh;
}

function fichasSkuChave_(s) {
  return String(s || '').trim().toLowerCase();
}

function fichasSkuLinhas_(sh) {
  const n = sh.getLastRow();
  if (n < 2) return [];
  return sh.getRange(2, 1, n - 1, 3).getDisplayValues().map((l, i) => ({ linha: i + 2, sku: String(l[0] || '').trim(), texto: String(l[1] || ''), data: String(l[2] || '') }));
}

function fichasSkuLer_(skus) {
  if (!Array.isArray(skus) || skus.length > 50) throw Error('Lista de SKUs inválida.');
  const quero = new Set(skus.map(fichasSkuChave_).filter(s => s));
  const fichas = {};
  fichasSkuLinhas_(fichasSkuAba_()).forEach(l => {
    if (l.sku && l.texto && quero.has(fichasSkuChave_(l.sku))) fichas[fichasSkuChave_(l.sku)] = { texto: l.texto, atualizado: l.data };
  });
  return { ok: true, fichas };
}

function fichasSkuSalvar_(sku, texto) {
  sku = String(sku || '').trim();
  texto = String(texto == null ? '' : texto);
  if (!sku || sku.length > 80 || /[\r\n]/.test(sku)) throw Error('SKU inválido.');
  if (texto.length > 2000) throw Error('Texto grande demais (máximo 2000 caracteres).');
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = fichasSkuAba_();
    const achada = fichasSkuLinhas_(sh).find(l => fichasSkuChave_(l.sku) === fichasSkuChave_(sku));
    const linha = achada ? achada.linha : Math.max(sh.getLastRow(), 1) + 1;
    const agora = Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'dd/MM/yyyy HH:mm');
    sh.getRange(linha, 1, 1, 3).setNumberFormat('@').setValues([[achada ? achada.sku : sku, texto, agora]]);   // texto puro: nada vira fórmula
    return { ok: true, criada: !achada };
  } finally {
    lock.releaseLock();
  }
}
