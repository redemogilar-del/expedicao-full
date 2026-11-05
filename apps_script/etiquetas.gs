// Acréscimo ao Apps Script "ML HUB MOGILAR — GOOGLE SHEETS" (o mesmo do EXP_SHEETS_URL): controle das etiquetas da aba "Vendas do dia".
//
// 1) No switch (b.acao) do doPost, acrescente (junto do 'vendas_relatorio'):
//
//      case 'etiquetas_registrar':
//        r = etiquetasRegistrar_(b);
//        break;
//      case 'etiquetas_ler':
//        r = etiquetasLer_(b);
//        break;
//
// 2) Cole as funções abaixo no final do Código.gs.
// 3) Implantar > Gerenciar implantações > lápis > Nova versão > Implantar (a URL não muda).
//
// A planilha passa a ter a aba "Etiquetas" com UMA linha por pedido (conta|pedido). Marcar de novo o mesmo pedido
// ATUALIZA a linha (por exemplo de Gerada para Cancelada), nunca repete.

var ET_CONTAS_ = ['ML19', 'ML44'];
var ET_CAB_ = ['Chave', 'Data e hora', 'Conta', 'Pedido', 'Nº do envio', 'SKU', 'Quantidade', 'Situação'];

function etAba_() {
  const ss = wb_();
  let sh = ss.getSheetByName('Etiquetas');

  if (!sh) {
    sh = ss.insertSheet('Etiquetas');
    sh.getRange(1, 1, 1, ET_CAB_.length).setValues([ET_CAB_]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }

  return sh;
}

function etiquetasRegistrar_(b) {
  const regs = b.registros || [];

  if (!regs.length || regs.length > 200) {
    throw Error('Lista de vendas inválida.');
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const sh = etAba_();
    const ultima = sh.getLastRow();
    const linhaDe = {};

    if (ultima >= 2) {
      sh.getRange(2, 1, ultima - 1, 1).getValues().forEach(function (l, i) {
        if (String(l[0]).trim() !== '') linhaDe[String(l[0])] = i + 2;
      });
    }

    const hora = Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'dd/MM/yyyy HH:mm');
    let gravados = 0;
    let atualizados = 0;
    const novas = [];

    regs.forEach(function (x) {
      if (ET_CONTAS_.indexOf(String(x.conta)) < 0 || String(x.pedido || '').trim() === '') return;
      if (x.situacao !== 'GERADA' && x.situacao !== 'CANCELADA') return;

      const chave = x.conta + '|' + String(x.pedido).trim();
      const linha = [chave, hora, String(x.conta), String(x.pedido).trim(), String(x.envio_id || ''), String(x.sku || ''),
        x.quantidade === null || x.quantidade === undefined ? '' : String(x.quantidade), x.situacao === 'GERADA' ? 'Gerada' : 'Cancelada'];

      if (linhaDe[chave]) {
        sh.getRange(linhaDe[chave], 1, 1, ET_CAB_.length).setNumberFormat('@').setValues([linha]);       // tudo texto: nada vira fórmula nem perde zeros
        atualizados++;
      } else {
        linhaDe[chave] = -1;       // mesmo pedido duas vezes no mesmo envio: só entra uma linha
        novas.push(linha);
        gravados++;
      }
    });

    if (novas.length) {
      sh.getRange(sh.getLastRow() + 1, 1, novas.length, ET_CAB_.length).setNumberFormat('@').setValues(novas);
    }

    SpreadsheetApp.flush();
    return { ok: true, gravados: gravados, atualizados: atualizados };
  } finally {
    lock.releaseLock();
  }
}

function etiquetasLer_(b) {
  const conta = String(b.conta || '');

  if (ET_CONTAS_.indexOf(conta) < 0) {
    throw Error('Conta inválida.');
  }

  const quer = {};
  (b.pedidos || []).forEach(function (p) { quer[String(p).trim()] = true; });

  const sh = wb_().getSheetByName('Etiquetas');
  const saida = {};

  if (sh && sh.getLastRow() >= 2) {
    sh.getRange(2, 1, sh.getLastRow() - 1, ET_CAB_.length).getValues().forEach(function (l) {
      if (String(l[2]) !== conta || !quer[String(l[3]).trim()]) return;
      saida[String(l[3]).trim()] = { situacao: String(l[7]) === 'Cancelada' ? 'CANCELADA' : 'GERADA', data: String(l[1]) };
    });
  }

  return { ok: true, etiquetas: saida };
}
