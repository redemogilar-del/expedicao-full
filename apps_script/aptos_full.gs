// Acréscimo ao Apps Script "ML HUB MOGILAR — GOOGLE SHEETS" (V7).
// Faz o painel Expedição Mogilar ler a aba "Aptos Para FULL" (coluna A, SKU, da linha 2 em diante).
//
// 1) No switch (b.acao) do doPost, acrescente:
//
//      case 'aptos_full':
//        r = aptosFull_();
//        break;
//
// 2) Cole a função abaixo no final do Código.gs.
// 3) Implantar > Gerenciar implantações > lápis > Nova versão > Implantar (a URL não muda).

function aptosFull_() {
  const alvo = 'aptos para full';

  const sh = wb_().getSheets().find(
    x => String(x.getName()).trim().toLowerCase() === alvo
  );

  if (!sh) {
    throw Error('Aba Aptos Para FULL não encontrada.');
  }

  const n = sh.getLastRow();

  if (n < 2) {
    return { ok: true, skus: [] };
  }

  const skus = sh.getRange(2, 1, n - 1, 1)
    .getDisplayValues()
    .map(l => String(l[0] || '').trim())
    .filter(s => s);

  return { ok: true, skus };
}
