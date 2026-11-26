#!/usr/bin/env node
/*
 * md_vers_word.js — Markdown balisé → rapport Word (.docx) à mise en page fixe
 * Diagnostic financier · Journées Portes Ouvertes DC-KNOWING
 *
 * Usage : node md_vers_word.js rapport.md rapport.docx
 *
 * SYNTAXE RECONNUE (voir GUIDE_MARKDOWN.txt pour le détail)
 *   ---  front matter  ---        entreprise, periode, date, reference
 *   ===PAGE===                    saut de page
 *   # Titre de page               titre de page (une fois par page)
 *   ## Section / ### Sous-section
 *   paragraphe (**gras** autorisé), - puces, 1. listes numérotées
 *   | tableau | GFM |             cellule commençant par {vert} {orange} {rouge} {gris} = pastille de couleur
 *   > [!AVIS|INFO|CONSEIL|ALERTE|OK] Titre   puis lignes "> texte"   = encadré
 *   ```scores``` ```indicateur``` ```graphique repartition``` ```graphique comparaison``` ```action```
 */
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun, Header, Footer,
  AlignmentType, WidthType, BorderStyle, ShadingType, LevelFormat, PageNumber, TabStopType,
  VerticalAlign, PageBreak, HeightRule, LineRuleType,
} = require("docx");
const { Resvg } = require("@resvg/resvg-js");

// ------------------------------------------------------------------ charte
const C = {
  ink: "000000", soft: "262626", slate: "525252", line: "E0E0E0", track: "EDEDED",
  gold: "FFD700", goldLight: "FEF9E7", goldDark: "785C03",
  ok: "15803D", okBg: "DCFCE7", warn: "92400E", warnBg: "FEF3C7", bad: "DC2626", badBg: "FEE2E2",
};
const STATUT = {
  vert: { fg: C.ok, bg: C.okBg, mot: "Bon" }, orange: { fg: C.warn, bg: C.warnBg, mot: "À surveiller" },
  rouge: { fg: C.bad, bg: C.badBg, mot: "À corriger" }, gris: { fg: C.slate, bg: C.track, mot: "Non évalué" },
};
const FONT = "Arial";
const PAGE_W = 11906, MARGE = 1080, LARGEUR = PAGE_W - 2 * MARGE; // 9746 DXA
const ICI = __dirname;
// Polices des graphiques : Liberation Sans (Linux) ou Arial (Windows / macOS), la première trouvée.
const FONT_SETS = [
  // Polices fournies avec le projet (word/fonts, licence SIL OFL) : indispensables sur un serveur sans polices (Vercel)
  { family: "Liberation Sans", files: [path.join(__dirname, "fonts", "LiberationSans-Regular.ttf"), path.join(__dirname, "fonts", "LiberationSans-Bold.ttf")] },
  { family: "Liberation Sans", files: ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"] },
  { family: "Arial", files: [`${process.env.WINDIR || "C:\\Windows"}\\Fonts\\arial.ttf`, `${process.env.WINDIR || "C:\\Windows"}\\Fonts\\arialbd.ttf`] },
  { family: "Arial", files: ["/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"] },
];
const FONT_SET = FONT_SETS.find((s) => s.files.some(fs.existsSync)) || FONT_SETS[0];
const FONT_FILES = FONT_SET.files.filter(fs.existsSync);

// ------------------------------------------------------------------ texte en ligne
function runs(txt, base = {}) {
  const out = [];
  const parts = String(txt).split(/(\*\*[^*]+\*\*)/g);
  for (const p of parts) {
    if (!p) continue;
    if (p.startsWith("**") && p.endsWith("**")) out.push(new TextRun({ text: p.slice(2, -2), bold: true, font: FONT, ...base }));
    else out.push(new TextRun({ text: p, font: FONT, ...base }));
  }
  return out;
}
const para = (txt, opt = {}, runOpt = {}) => new Paragraph({ children: runs(txt, runOpt), spacing: { after: 120, line: 288, lineRule: LineRuleType.AUTO }, ...opt });

const NONE = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const NOB = { top: NONE, bottom: NONE, left: NONE, right: NONE };
const cell = (children, width, extra = {}) => new TableCell({ children, width: { size: width, type: WidthType.DXA }, borders: NOB, margins: { top: 100, bottom: 100, left: 160, right: 160 }, ...extra });
const table1 = (children, fill, borders = NOB, w = LARGEUR) => new Table({
  width: { size: w, type: WidthType.DXA }, columnWidths: [w],
  rows: [new TableRow({ cantSplit: true, children: [cell(children, w, { shading: { fill, type: ShadingType.CLEAR, color: "auto" }, borders, margins: { top: 140, bottom: 140, left: 220, right: 220 } })] })],
});
const space = (after = 120) => new Paragraph({ children: [], spacing: { after: 0, before: 0, line: Math.max(after, 40), lineRule: LineRuleType.EXACT } });

// ------------------------------------------------------------------ graphiques (SVG → PNG)
function png(svg, wPx) {
  const r = new Resvg(svg, { fitTo: { mode: "width", value: wPx * 2 }, font: { fontFiles: FONT_FILES, loadSystemFonts: false, defaultFontFamily: FONT_SET.family } });
  return r.render().asPng();
}
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const nb = (s) => parseFloat(String(s).replace(",", "."));
const frn = (x) => String(Math.round(x * 10) / 10).replace(".", ",");

function graphRepartition(lignes) {
  const W = 650, H = 118;
  const items = lignes.map((l) => { const [lab, val, col] = l.split("|").map((s) => s.trim()); return { lab, val: nb(val), col: col || "#A3A3A3" }; });
  const tot = items.reduce((a, b) => a + b.val, 0);
  let x = 0; let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`;
  for (const it of items) {
    const w = (W * it.val) / tot;
    s += `<rect x="${x}" y="4" width="${Math.max(w - 2, 2)}" height="40" fill="${it.col}"/>`;
    const lab = `${it.lab} ${frn(it.val)}`;
    if (w > lab.length * 7 + 16) s += `<text x="${x + 10}" y="30" font-family="Liberation Sans" font-weight="bold" font-size="14" fill="${it.col === "#FFD700" || it.col.toUpperCase() > "#9" ? "#000" : "#fff"}">${esc(lab)}</text>`;
    x += w;
  }
  // légende complète sous la barre
  let lx = 0, ly = 72;
  for (const it of items) {
    const t = `${it.lab} : ${frn(it.val)}`; const tw = t.length * 7.2 + 34;
    if (lx + tw > W) { lx = 0; ly += 26; }
    s += `<rect x="${lx}" y="${ly - 12}" width="14" height="14" rx="3" fill="${it.col}" stroke="#A3A3A3" stroke-width="0.6"/>`;
    s += `<text x="${lx + 20}" y="${ly}" font-family="Liberation Sans" font-size="13" fill="#000">${esc(t)}</text>`;
    lx += tw;
  }
  return s.replace(`height="${H}"`, `height="${ly + 10}"`).replace(`0 0 ${W} ${H}`, `0 0 ${W} ${ly + 10}`) + "</svg>";
}

function graphComparaison(lignes, unite) {
  const items = lignes.map((l) => { const [lab, val, rep, st] = l.split("|").map((s) => s.trim()); return { lab, val: nb(val), rep: nb(String(rep).replace(/[^0-9.,]/g, "")), repLab: rep, st: st || "gris" }; });
  const COL = { vert: "#15803D", orange: "#E8A200", rouge: "#DC2626", gris: "#A3A3A3" };
  const W = 650, row = 62, H = items.length * row + 6, X0 = 250, X1 = W - 20;
  const mx = Math.max(...items.map((i) => Math.max(i.val, i.rep))) * 1.25;
  const X = (v) => X0 + ((X1 - X0) * v) / mx;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`;
  items.forEach((it, i) => {
    const y = i * row + 8; const col = COL[it.st] || COL.gris;
    s += `<text x="0" y="${y + 14}" font-family="Liberation Sans" font-weight="bold" font-size="14" fill="#000">${esc(it.lab)}</text>`;
    s += `<text x="0" y="${y + 36}" font-family="Liberation Sans" font-weight="bold" font-size="18" fill="${col}">${frn(it.val)} ${esc(unite)}</text>`;
    s += `<rect x="${X0}" y="${y + 6}" width="${X1 - X0}" height="20" rx="10" fill="#EDEDED"/>`;
    s += `<rect x="${X0}" y="${y + 6}" width="${X(it.val) - X0}" height="20" rx="10" fill="${col}"/>`;
    s += `<line x1="${X(it.rep)}" y1="${y}" x2="${X(it.rep)}" y2="${y + 32}" stroke="#000" stroke-width="2.5"/>`;
    s += `<text x="${X(it.rep)}" y="${y + 48}" text-anchor="middle" font-family="Liberation Sans" font-size="12" fill="#525252">${esc(it.repLab)}</text>`;
  });
  return s + "</svg>";
}

function image(svg, wPx) {
  const m = svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
  const ratio = nb(m[2]) / nb(m[1]);
  const data = png(svg, wPx);
  if (process.env.DUMP) fs.writeFileSync(`dump_${Date.now()}_${Math.random().toString(36).slice(2, 6)}.png`, data);
  return new Paragraph({ children: [new ImageRun({ type: "png", data, transformation: { width: wPx, height: Math.round(wPx * ratio) } })], spacing: { after: 160 }, keepLines: true });
}

// ------------------------------------------------------------------ blocs spéciaux
function kv(lignes) { const o = {}; for (const l of lignes) { const i = l.indexOf(":"); if (i > 0) o[l.slice(0, i).trim()] = l.slice(i + 1).trim(); } return o; }

function blocScores(lignes) {
  const items = lignes.map((l) => l.split("|").map((s) => s.trim()));
  const w = Math.floor(LARGEUR / items.length) - 60;
  const cells = items.map(([nom, score, note, phrase]) => cell([
    new Paragraph({ children: [new TextRun({ text: nom.toUpperCase(), font: FONT, bold: true, size: 17, color: C.slate, characterSpacing: 20 })], spacing: { after: 40 } }),
    new Paragraph({ children: [new TextRun({ text: score, font: FONT, bold: true, size: 56 }), new TextRun({ text: " / 100   ", font: FONT, size: 22, color: C.slate }),
      new TextRun({ text: ` ${note} `, font: FONT, bold: true, size: 30, color: C.gold, shading: { type: ShadingType.CLEAR, fill: C.ink, color: "auto" } })], spacing: { after: 60 } }),
    new Paragraph({ children: runs(phrase, { size: 21 }), spacing: { after: 0, line: 276, lineRule: LineRuleType.AUTO } }),
  ], w, { shading: { fill: "FFFFFF", type: ShadingType.CLEAR, color: "auto" }, borders: { top: { style: BorderStyle.SINGLE, size: 24, color: C.gold }, bottom: { style: BorderStyle.SINGLE, size: 4, color: C.line }, left: { style: BorderStyle.SINGLE, size: 4, color: C.line }, right: { style: BorderStyle.SINGLE, size: 4, color: C.line } }, margins: { top: 160, bottom: 160, left: 220, right: 220 } }));
  const gap = 120;
  const colW = []; const rowCells = [];
  cells.forEach((c, i) => { if (i) { colW.push(gap); rowCells.push(cell([new Paragraph({ children: [] })], gap)); } colW.push(w); rowCells.push(c); });
  const tot = colW.reduce((a, b) => a + b, 0);
  return [new Table({ width: { size: tot, type: WidthType.DXA }, columnWidths: colW, rows: [new TableRow({ cantSplit: true, children: rowCells })] }), space(160)];
}

function blocIndicateur(lignes) {
  const o = kv(lignes); const st = STATUT[o.statut] || STATUT.gris;
  const wl = 2700, wr = LARGEUR - wl;
  const left = cell([
    new Paragraph({ children: [new TextRun({ text: (o.titre || "").toUpperCase(), font: FONT, bold: true, size: 17, color: C.slate, characterSpacing: 20 })], spacing: { after: 60 } }),
    new Paragraph({ children: [new TextRun({ text: o.valeur || "", font: FONT, bold: true, size: 30 })], spacing: { after: 80, line: 260, lineRule: LineRuleType.AUTO } }),
    new Paragraph({ children: [new TextRun({ text: ` ${o.mot || st.mot} `, font: FONT, bold: true, size: 18, color: st.fg, shading: { type: ShadingType.CLEAR, fill: st.bg, color: "auto" } })], spacing: { after: 0 } }),
  ], wl, { shading: { fill: C.goldLight, type: ShadingType.CLEAR, color: "auto" }, borders: { ...NOB, left: { style: BorderStyle.SINGLE, size: 36, color: st.fg } }, verticalAlign: VerticalAlign.CENTER, margins: { top: 140, bottom: 140, left: 200, right: 160 } });
  const rightParas = [];
  if (o.texte) rightParas.push(new Paragraph({ children: runs(o.texte), spacing: { after: o.conseil ? 80 : 0, line: 288, lineRule: LineRuleType.AUTO } }));
  if (o.conseil) rightParas.push(new Paragraph({ children: [new TextRun({ text: "Notre conseil : ", font: FONT, bold: true, color: C.goldDark }), ...runs(o.conseil)], spacing: { after: 0, line: 288, lineRule: LineRuleType.AUTO } }));
  const right = cell(rightParas, wr, { borders: { ...NOB, bottom: { style: BorderStyle.SINGLE, size: 4, color: C.line } }, verticalAlign: VerticalAlign.CENTER, margins: { top: 140, bottom: 140, left: 240, right: 120 } });
  return [new Table({ width: { size: LARGEUR, type: WidthType.DXA }, columnWidths: [wl, wr], rows: [new TableRow({ cantSplit: true, children: [left, right] })] }), space(200)];
}

function blocAction(lignes, numero) {
  const o = kv(lignes);
  const prio = parseInt(o.priorite || "2", 10);
  const fill = prio <= 1 ? C.bad : prio === 2 ? C.ink : C.gold;
  const txt = prio <= 2 ? "FFFFFF" : C.ink;
  const lab = prio <= 1 ? "URGENT" : prio === 2 ? "IMPORTANT" : "UTILE";
  const wl = 1900, wr = LARGEUR - wl;
  const lp = [new Paragraph({ children: [new TextRun({ text: `${numero}  `, font: FONT, bold: true, size: 30, color: txt }), new TextRun({ text: lab, font: FONT, bold: true, size: 15, color: txt, characterSpacing: 20 })], spacing: { after: 60 } })];
  if (o.delai) lp.push(new Paragraph({ children: [new TextRun({ text: o.delai, font: FONT, size: 18, color: txt })], spacing: { after: 20, line: 240, lineRule: LineRuleType.AUTO } }));
  if (o.outil) lp.push(new Paragraph({ children: [new TextRun({ text: o.outil, font: FONT, bold: true, size: 18, color: prio <= 2 ? C.gold : C.ink })], spacing: { after: 0, line: 240, lineRule: LineRuleType.AUTO } }));
  const left = cell(lp, wl, { shading: { fill, type: ShadingType.CLEAR, color: "auto" }, verticalAlign: VerticalAlign.CENTER, margins: { top: 100, bottom: 100, left: 180, right: 120 } });
  const ps = [new Paragraph({ children: [new TextRun({ text: o.titre || "", font: FONT, bold: true, size: 23 })], spacing: { after: 40 }, keepNext: true })];
  if (o.pourquoi) ps.push(new Paragraph({ children: [new TextRun({ text: "Pourquoi : ", font: FONT, bold: true }), ...runs(o.pourquoi)], spacing: { after: 20, line: 264, lineRule: LineRuleType.AUTO } }));
  if (o.comment) ps.push(new Paragraph({ children: [new TextRun({ text: "Comment : ", font: FONT, bold: true }), ...runs(o.comment)], spacing: { after: 20, line: 264, lineRule: LineRuleType.AUTO } }));
  if (o.gain) ps.push(new Paragraph({ children: [new TextRun({ text: "Ce que vous y gagnez : ", font: FONT, bold: true, color: C.goldDark }), ...runs(o.gain, { bold: true })], spacing: { after: 0, line: 264, lineRule: LineRuleType.AUTO } }));
  const right = cell(ps, wr, { borders: { ...NOB, bottom: { style: BorderStyle.SINGLE, size: 4, color: C.line } }, verticalAlign: VerticalAlign.CENTER, margins: { top: 100, bottom: 100, left: 220, right: 120 } });
  return [new Table({ width: { size: LARGEUR, type: WidthType.DXA }, columnWidths: [wl, wr], rows: [new TableRow({ cantSplit: true, children: [left, right] })] }), space(180)];
}

const CALLOUT = {
  AVIS: { fill: C.ink, titre: C.gold, txt: "FFFFFF" }, INFO: { fill: C.goldLight, titre: C.goldDark, txt: C.ink },
  CONSEIL: { fill: C.goldLight, titre: C.goldDark, txt: C.ink, bord: C.gold }, ALERTE: { fill: C.badBg, titre: C.bad, txt: C.ink, bord: C.bad },
  OK: { fill: C.okBg, titre: C.ok, txt: C.ink, bord: C.ok },
};
function blocEncadre(type, titre, lignes) {
  const t = CALLOUT[type] || CALLOUT.INFO;
  const ch = [];
  if (titre) ch.push(new Paragraph({ children: [new TextRun({ text: titre, font: FONT, bold: true, size: 24, color: t.titre })], spacing: { after: 80 }, keepNext: true }));
  let liste = [];
  const flush = () => { liste.forEach((x) => ch.push(new Paragraph({ numbering: { reference: "puces", level: 0 }, children: runs(x, { color: t.txt }), spacing: { after: 60, line: 288, lineRule: LineRuleType.AUTO } }))); liste = []; };
  for (const l of lignes) {
    if (/^- /.test(l)) { liste.push(l.slice(2)); continue; }
    flush();
    if (l.trim()) ch.push(new Paragraph({ children: runs(l, { color: t.txt }), spacing: { after: 80, line: 288, lineRule: LineRuleType.AUTO } }));
  }
  flush();
  const borders = t.bord ? { ...NOB, left: { style: BorderStyle.SINGLE, size: 36, color: t.bord } } : NOB;
  return [table1(ch, t.fill, borders), space(160)];
}

function tableau(lignes) {
  const rows = lignes.filter((l) => !/^\|\s*:?-{2,}/.test(l)).map((l) => l.replace(/^\||\|$/g, "").split("|").map((s) => s.trim()));
  const n = rows[0].length;
  // largeurs : proportionnelles au contenu, bornées
  const lens = Array.from({ length: n }, (_, j) => Math.max(...rows.map((r) => (r[j] || "").length)));
  const sum = lens.reduce((a, b) => a + Math.min(Math.max(b, 8), 60), 0);
  let widths = lens.map((l) => Math.floor((LARGEUR * Math.min(Math.max(l, 8), 60)) / sum));
  widths[n - 1] += LARGEUR - widths.reduce((a, b) => a + b, 0);
  const trs = rows.map((r, i) => new TableRow({
    cantSplit: true, tableHeader: i === 0,
    children: r.map((c, j) => {
      const m = c.match(/^\{(vert|orange|rouge|gris)\}\s*(.*)$/);
      const st = m ? STATUT[m[1]] : null; const txt = m ? m[2] : c;
      const head = i === 0;
      return new TableCell({
        width: { size: widths[j], type: WidthType.DXA },
        shading: { fill: head ? C.ink : st ? st.bg : "FFFFFF", type: ShadingType.CLEAR, color: "auto" },
        borders: { top: NONE, left: NONE, right: NONE, bottom: { style: BorderStyle.SINGLE, size: 4, color: C.line } },
        margins: { top: 90, bottom: 90, left: 140, right: 140 }, verticalAlign: VerticalAlign.CENTER,
        children: [new Paragraph({ keepNext: i < rows.length - 1, keepLines: true, children: runs(txt, head ? { bold: true, color: "FFFFFF", size: 20 } : st ? { bold: true, color: st.fg, size: 21 } : { size: 21 }), spacing: { after: 0, line: 264, lineRule: LineRuleType.AUTO } })],
      });
    }),
  }));
  return [new Table({ width: { size: LARGEUR, type: WidthType.DXA }, columnWidths: widths, rows: trs }), space(160)];
}

// ------------------------------------------------------------------ analyse du Markdown
function lire(md) {
  let meta = {};
  md = md.replace(/\r/g, "");
  const fm = md.match(/^---\n([\s\S]*?)\n---\n/);
  if (fm) { meta = kv(fm[1].split("\n")); md = md.slice(fm[0].length); }
  const L = md.split("\n");
  const out = [];
  let i = 0, num = 0, actionNo = 0, listeNum = 0;
  let parBuf = [];
  const flushPar = () => { if (parBuf.length) { out.push(para(parBuf.join(" "))); parBuf = []; } };
  while (i < L.length) {
    const l = L[i];
    if (/^```/.test(l)) {
      flushPar();
      const kind = l.slice(3).trim().split(/\s+/);
      const body = []; i++;
      while (i < L.length && !/^```/.test(L[i])) { if (L[i].trim()) body.push(L[i].trim()); i++; }
      i++;
      if (kind[0] === "scores") out.push(...blocScores(body));
      else if (kind[0] === "indicateur") out.push(...blocIndicateur(body));
      else if (kind[0] === "action") out.push(...blocAction(body, ++actionNo));
      else if (kind[0] === "graphique") {
        const titreL = body.find((b) => b.startsWith("titre:")); const uniteL = body.find((b) => b.startsWith("unite:"));
        const data = body.filter((b) => !/^(titre|unite):/.test(b));
        if (titreL) out.push(new Paragraph({ children: [new TextRun({ text: titreL.slice(6).trim(), font: FONT, bold: true, size: 22 })], spacing: { after: 80 }, keepNext: true }));
        out.push(image(kind[1] === "comparaison" ? graphComparaison(data, uniteL ? uniteL.slice(6).trim() : "") : graphRepartition(data), 600));
      }
      continue;
    }
    if (l.trim() === "===PAGE===") { flushPar(); out.push(new Paragraph({ children: [new PageBreak()] })); i++; continue; }
    if (/^# /.test(l)) {
      flushPar();
      out.push(new Paragraph({ children: [new TextRun({ text: l.slice(2), font: FONT, bold: true, size: 40 })], spacing: { before: 0, after: 60 }, keepNext: true }));
      out.push(new Paragraph({ children: [], border: { bottom: { style: BorderStyle.SINGLE, size: 24, color: C.gold, space: 1 } }, spacing: { after: 200 }, indent: { right: LARGEUR - 1400 } }));
      i++; continue;
    }
    if (/^## /.test(l)) { flushPar(); out.push(new Paragraph({ children: [new TextRun({ text: l.slice(3), font: FONT, bold: true, size: 27 })], spacing: { before: 200, after: 100 }, keepNext: true })); i++; continue; }
    if (/^### /.test(l)) { flushPar(); out.push(new Paragraph({ children: [new TextRun({ text: l.slice(4), font: FONT, bold: true, size: 23, color: C.goldDark })], spacing: { before: 120, after: 60 }, keepNext: true })); i++; continue; }
    if (/^> \[!/.test(l)) {
      flushPar();
      const m = l.match(/^> \[!(\w+)\]\s*(.*)$/); const body = []; i++;
      while (i < L.length && /^>/.test(L[i])) { body.push(L[i].replace(/^>\s?/, "")); i++; }
      out.push(...blocEncadre(m[1], m[2], body)); continue;
    }
    if (/^\|/.test(l)) { flushPar(); const t = []; while (i < L.length && /^\|/.test(L[i])) { t.push(L[i]); i++; } out.push(...tableau(t)); continue; }
    if (/^- /.test(l)) { flushPar(); out.push(new Paragraph({ numbering: { reference: "puces", level: 0 }, children: runs(l.slice(2)), spacing: { after: 80, line: 288, lineRule: LineRuleType.AUTO } })); i++; continue; }
    if (/^\d+\. /.test(l)) {
      flushPar();
      if (!(i > 0 && /^\d+\. /.test(L[i - 1]))) listeNum++;
      out.push(new Paragraph({ numbering: { reference: "num", level: 0, instance: listeNum }, children: runs(l.replace(/^\d+\. /, "")), spacing: { after: 80, line: 288, lineRule: LineRuleType.AUTO } }));
      i++; continue;
    }
    if (!l.trim()) { flushPar(); i++; continue; }
    if (/^<!-- mention -->/.test(l)) { flushPar(); i++; const b = []; while (i < L.length && L[i].trim()) { b.push(L[i].trim()); i++; }
      out.push(new Paragraph({ children: runs(b.join(" "), { size: 17, color: C.slate }), spacing: { before: 60, after: 0, line: 252, lineRule: LineRuleType.AUTO } })); continue; }
    if (/^<!--/.test(l)) { while (i < L.length && !/-->/.test(L[i])) i++; i++; continue; } // commentaires ignorés
    parBuf.push(l.trim()); i++;
  }
  flushPar();
  return { meta, out };
}

// ------------------------------------------------------------------ document
/** Markdown balisé (texte) → document Word (Buffer). Utilisé par la CLI et par la fonction Vercel api/docx.js. */
async function convertir(markdown) {
  const { meta, out } = lire(markdown);
  const logo = fs.readFileSync(path.join(ICI, "logo.png"));
  const header = new Header({ children: [
    new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: LARGEUR }],
      children: [new ImageRun({ type: "png", data: logo, transformation: { width: 128, height: 41 } }),
        new TextRun({ text: "\t", font: FONT }),
        new TextRun({ text: "Journées Portes Ouvertes · 16-18 décembre 2026", font: FONT, size: 17, bold: true, color: C.slate })],
      border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: C.ink, space: 6 } }, spacing: { after: 0 },
    }),
  ] });
  const footer = new Footer({ children: [new Paragraph({
    tabStops: [{ type: TabStopType.RIGHT, position: LARGEUR }],
    children: [new TextRun({ text: `${meta.entreprise || ""} · Diagnostic confidentiel · Réf. ${meta.reference || ""}`, font: FONT, size: 16, color: C.slate }),
      new TextRun({ text: "\tPage ", font: FONT, size: 16, color: C.slate }), new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, bold: true }),
      new TextRun({ text: " / ", font: FONT, size: 16, color: C.slate }), new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT, size: 16, bold: true })],
  })] });
  const doc = new Document({
    creator: "DC-KNOWING", title: `Diagnostic financier — ${meta.entreprise || ""}`,
    styles: { default: { document: { run: { font: FONT, size: 22 }, paragraph: { spacing: { line: 288, lineRule: LineRuleType.AUTO } } } } },
    numbering: { config: [
      { reference: "puces", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 360, hanging: 260 } }, run: { color: C.goldDark, bold: true } } }] },
      { reference: "num", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 360, hanging: 300 } }, run: { bold: true } } }] },
    ] },
    sections: [{
      properties: { page: { size: { width: PAGE_W, height: 16838 }, margin: { top: 1560, bottom: 1000, left: MARGE, right: MARGE, header: 620, footer: 500 } } },
      headers: { default: header }, footers: { default: footer }, children: out,
    }],
  });
  return Packer.toBuffer(doc);
}

async function main(src, dst) {
  fs.writeFileSync(dst, await convertir(fs.readFileSync(src, "utf8")));
  console.log("OK", dst);
}

module.exports = { convertir };
if (require.main === module) main(process.argv[2], process.argv[3]).catch((e) => { console.error(e); process.exit(1); });
