// Acréscimo ao Apps Script "ML HUB MOGILAR — GOOGLE SHEETS" (o mesmo do EXP_SHEETS_URL): registro das pausas por SKU da aba "Trocar SKU".
// (Já está dentro do Codigo.gs V30 do repositório ml-hub-anuncios-teste. Use este arquivo só se o seu script for outro.)
//
// 1) No switch (b.acao) do doPost, acrescente:
//
//      case 'pausas_registrar':
//        r = pausasRegistrar_(b);
//        break;
//      case 'pausas_ler':
//        r = pausasLer_();
//        break;
//
// 2) Cole as funções abaixo no final do Código.gs.
// 3) Implantar > Gerenciar implantações > lápis > Nova versão > Implantar (a URL não muda).
//
// A aba "Pausas por SKU" tem UMA linha por SKU enquanto ele estiver pausado (pausar de novo o mesmo SKU soma na mesma linha).
// Quando o SKU é reativado, a mesma linha recebe a data da reativação e fica "Reativado". Uma pausa nova depois disso abre outra linha
// (o histórico fica na aba).

var PS_ABA_ = 'Pausas por SKU';
var PS_CAB_ = ['SKU', 'Pausados', 'Com erro ao pausar', 'Data da atualização', 'Motivo', 'Previsão de chegada (reativar)',
  'Data da reativação', 'Reativados', 'Com erro ao reativar', 'Situação', 'Contas'];
var PS_MOTIVOS_ = { FALTA_ESTOQUE: 'Falta de estoque', DESCONTINUADO: 'Produto descontinuado' };

function psAba_() {
  const ss = wb_();
  let sh = ss.getSheetByName(PS_ABA_);

  if (!sh) {
    sh = ss.insertSheet(PS_ABA_);
    sh.getRange(1, 1, 1, PS_CAB_.length).setValues([PS_CAB_]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }

  return sh;
}

function psChave_(s) {
  return String(s === null || s === undefined ? '' : s).replace(/^'/, '').trim().toUpperCase();
}

function psTexto_(s) {
  s = String(s === null || s === undefined ? '' : s);
  return /^[=+\-@]/.test(s) ? "'" + s : s;       // nada vira fórmula
}

function psNum_(v) {
  const n = Number(v);
  return isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

function pausasRegistrar_(b) {
  const tipo = b.tipo;
  const sku = String(b.sku || '').trim();

  if (tipo !== 'pausa' && tipo !== 'reativacao') throw Error('Tipo de registro inválido.');
  if (!sku || sku.length > 100) throw Error('SKU inválido.');

  const ok = psNum_(b.ok);
  const erros = psNum_(b.erros);
  const contas = (b.contas || []).filter(function (c) { return c === 'ML19' || c === 'ML44'; });
  let motivo = '';
  let previsao = '';

  if (tipo === 'pausa') {
    motivo = PS_MOTIVOS_[b.motivo] || '';
    if (!motivo) throw Error('Motivo da pausa inválido.');

    if (b.motivo === 'FALTA_ESTOQUE') {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(b.previsao || ''));
      if (!m) throw Error('Data prevista de chegada inválida.');
      previsao = m[3] + '/' + m[2] + '/' + m[1];
    }
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const sh = psAba_();
    const ultima = sh.getLastRow();
    const chave = psChave_(sku);
    let linha = 0;
    let atual = null;

    if (ultima >= 2) {
      const dados = sh.getRange(2, 1, ultima - 1, PS_CAB_.length).getValues();

      for (let i = dados.length - 1; i >= 0; i--) {          // a linha mais recente do SKU que ainda não foi reativada
        if (psChave_(dados[i][0]) === chave && String(dados[i][9]) !== 'Reativado') {
          linha = i + 2;
          atual = dados[i];
          break;
        }
      }
    }

    const agora = Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'dd/MM/yyyy HH:mm');
    const juntar = function (antigas) {
      const todas = String(antigas || '').split(/[,\s]+/).filter(String).concat(contas);
      return todas.filter(function (c, i) { return todas.indexOf(c) === i; }).sort().join(', ');
    };
    let nova;

    if (tipo === 'pausa') {
      nova = [atual ? atual[0] : psTexto_(sku), psNum_(atual && atual[1]) + ok, erros, agora, motivo, previsao, '', '', '', 'Pausado', juntar(atual && atual[10])];
    } else {
      const reativados = psNum_(atual && atual[7]) + ok;
      nova = atual
        ? [atual[0], atual[1], atual[2], agora, atual[4], atual[5], agora, reativados, erros, erros ? 'Reativado em parte' : 'Reativado', juntar(atual[10])]
        : [psTexto_(sku), '', '', agora, '', '', agora, reativados, erros, erros ? 'Reativado em parte' : 'Reativado', juntar('')];
    }

    const destino = linha || sh.getLastRow() + 1;
    sh.getRange(destino, 1, 1, 1).setNumberFormat('@');
    sh.getRange(destino, 6, 1, 1).setNumberFormat('@');            // a data prevista fica como foi escrita (dd/mm/aaaa)
    sh.getRange(destino, 1, 1, PS_CAB_.length).setValues([nova]);

    return { ok: true, linha: destino, nova: !linha, situacao: nova[9] };
  } finally {
    lock.releaseLock();
  }
}

function pausasLer_() {
  const sh = wb_().getSheetByName(PS_ABA_);
  const saida = [];

  if (sh && sh.getLastRow() >= 2) {
    sh.getRange(2, 1, sh.getLastRow() - 1, PS_CAB_.length).getDisplayValues().forEach(function (l) {
      if (String(l[0]).trim() === '' || l[9] === 'Reativado') return;
      saida.push({ sku: String(l[0]).replace(/^'/, '').trim(), pausados: l[1], erros: l[2], data: l[3], motivo: l[4],
        previsao: l[5], reativados: l[7], situacao: l[9], contas: l[10] });
    });
  }

  return { ok: true, pausas: saida.reverse().slice(0, 500) };
}
