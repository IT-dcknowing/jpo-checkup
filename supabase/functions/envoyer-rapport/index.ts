// Edge Function « envoyer-rapport » — envoi des e-mails Check-up 360 par Resend.
//
// Corps JSON :
//   { "rapport_id": "<uuid>" }                       → e-mail du rapport (lien signé 7 jours, pièce jointe si < 5 Mo)
//   { "rapport_id": "<uuid>", "type": "excuse" }      → e-mail d'excuse après un échec de lecture
//   { "rapport_id": "<uuid>", "type": "rappel", "jour_long", "accroche", "programme": [], "entretien" } → veille (serveur)
//   { "type": "invitation", "nom", "jour_long", "horaire", "entretien", "ics_base64" } → invitation (à l'adresse de la session)
//
// Authentification (faite ici, verify_jwt désactivé) :
//   - jeton de session du visiteur : le rapport doit lui appartenir ; 10 envois par heure au plus ;
//   - serveur (API Vercel) : clé service_role identique à celle de la fonction, ou en-tête x-cle-interne égal au
//     secret EDGE_INTERNAL_SECRET (partagé avec Vercel). Accès à tous les rapports.
// Destinataire : toujours l'adresse ACTUELLE du compte (Supabase Auth), jamais une adresse figée au dépôt.
// Secrets (Supabase > Edge Functions > Secrets) : RESEND_API_KEY (obligatoire), MAIL_FROM, MAIL_COPY_TO, PUBLIC_SITE_URL,
// EDGE_INTERNAL_SECRET (recommandé), EMAILS_PAR_HEURE (défaut 10).
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.119.0";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY") ?? "";
const MAIL_FROM = Deno.env.get("MAIL_FROM") ?? "DC-KNOWING · Diagnostic JPO <diagnostic@dc-knowing.com>";
const MAIL_COPY_TO = (Deno.env.get("MAIL_COPY_TO") ?? "").split(",").map((x) => x.trim()).filter(Boolean);
const SITE = Deno.env.get("PUBLIC_SITE_URL") ?? "";
const INTERNE = Deno.env.get("EDGE_INTERNAL_SECRET") ?? "";
const EMAILS_PAR_HEURE = Number(Deno.env.get("EMAILS_PAR_HEURE") ?? "10") || 10;

const LIEN_VALIDITE_S = 7 * 24 * 3600;      // 7 jours
const PIECE_JOINTE_MAX = 5 * 1024 * 1024;   // 5 Mo
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const PIED = "27 22 42 14 43 / 07 67 13 19 93 · portaildck.dc-knowing.com · infos@dc-knowing.com · Cocody Riviera 2, Abidjan";
const ADRESSE = "Cocody Riviera 2, Abidjan";

const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false, autoRefreshToken: false } });

// La fonction n'est appelée que par l'API (serveur à serveur) : CORS limité au site, jamais « * ».
const CORS = {
  "Access-Control-Allow-Origin": SITE ? new URL(SITE).origin : "null",
  "Vary": "Origin",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });

const esc = (s: unknown) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function egal(a: string, b: string): boolean {
  if (!a || !b || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

// Clé serveur d'un autre format que celle de la fonction (eyJ... / sb_secret_...) : seule une clé serveur ouvre
// l'API d'administration des comptes ; un jeton de visiteur ou la clé publique y sont refusés.
async function estCleServeur(jeton: string): Promise<boolean> {
  let role = "";
  try {
    role = JSON.parse(atob(jeton.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).role;
  } catch (_) { /* pas un JWT */ }
  if (!jeton.startsWith("sb_secret_") && role !== "service_role") return false;
  try {
    const r = await fetch(`${SUPABASE_URL}/auth/v1/admin/users?per_page=1`, {
      headers: { apikey: jeton, Authorization: `Bearer ${jeton}` },
    });
    return r.ok;
  } catch (_) {
    return false;
  }
}

// Gabarit sobre aux couleurs de l'app (noir, or #FFD700), styles en ligne pour les messageries
function page(paras: string[], bouton?: [string, string], pied = ""): string {
  const p = paras.map((x) => `<p style="margin:0 0 12px;font-size:15px;line-height:1.55">${x}</p>`).join("");
  const b = bouton
    ? `<p style="margin:18px 0"><a href="${esc(bouton[1])}" style="display:inline-block;background:#FFD700;color:#000;padding:12px 22px;border-radius:12px;font-weight:700;text-decoration:none;border:1.5px solid #000">${esc(bouton[0])}</a></p>`
    : "";
  return `<!doctype html><html lang="fr"><body style="margin:0;background:#F5F5F5;padding:24px 12px">
<div style="font-family:Arial,Helvetica,sans-serif;color:#000;max-width:560px;margin:0 auto;padding:24px;background:#fff;border:1px solid #E0E0E0;border-radius:16px">
<p style="margin:0 0 18px;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase">DC-KNOWING · Journées Portes Ouvertes</p>
${p}${b}
<p style="margin:18px 0 0;padding-top:12px;border-top:1px solid #E0E0E0;font-size:11px;color:#525252">${esc(pied)}${pied ? "<br>" : ""}${esc(PIED)}</p>
</div></body></html>`;
}

// Adresse actuelle du compte : suit un changement d'adresse fait après le dépôt.
async function adresseActuelle(userId: string, repli: string): Promise<string> {
  try {
    const { data } = await admin.auth.admin.getUserById(userId);
    return data?.user?.email || repli;
  } catch (_) {
    return repli;
  }
}

type PieceJointe = { filename: string; content: string; content_type?: string };

async function envoyer(to: string, subject: string, html: string, text: string, attachments: PieceJointe[] = [], copie = false) {
  if (!RESEND_API_KEY) throw new Error("RESEND_API_KEY absente des secrets Supabase");
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: MAIL_FROM, to: [to], subject, html, text,
      ...(copie && MAIL_COPY_TO.length ? { cc: MAIL_COPY_TO } : {}),
      ...(attachments.length ? { attachments } : {}),
    }),
  });
  if (!r.ok) throw new Error(`Resend HTTP ${r.status} : ${(await r.text()).slice(0, 300)}`);
  return await r.json();
}

function base64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json(405, { error: "Méthode non autorisée" });

  // ---------------------------------------------------------------- qui appelle ?
  const jeton = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
  if (!jeton) return json(401, { error: "Authentification requise" });
  const serveur = egal(jeton, SERVICE_KEY) || (INTERNE !== "" && egal(req.headers.get("x-cle-interne") ?? "", INTERNE)) ||
    await estCleServeur(jeton);
  let user: { id: string; email?: string } | null = null;
  if (!serveur) {
    const { data, error } = await admin.auth.getUser(jeton);
    if (error || !data.user) return json(401, { error: "Session expirée" });
    user = data.user;
    // limite d'envois par visiteur (partagée en base avec l'API : fonction consommer_quota)
    const { data: permis, error: errQ } = await admin.rpc("consommer_quota",
      { p_cle: `email:${user.id}`, p_poids: 1, p_max: EMAILS_PAR_HEURE, p_fenetre_s: 3600 });
    if (errQ) console.error("quota e-mail", errQ);
    else if (!permis) return json(429, { error: "Trop d'envois : réessayez plus tard" });
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return json(400, { error: "Corps JSON attendu" });
  }
  const type = String(body.type ?? "rapport");

  // ---------------------------------------------------------------- invitation (toujours à l'adresse de la session)
  if (type === "invitation") {
    if (!user?.email) return json(403, { error: "Réservé au visiteur connecté" });
    const ics = String(body.ics_base64 ?? "");
    if (!ics || ics.length > 60_000) return json(400, { error: "Invitation agenda absente ou trop lourde" });
    const nom = String(body.nom ?? "").slice(0, 120);
    const jour = String(body.jour_long ?? "").slice(0, 80);
    const horaire = String(body.horaire ?? "").slice(0, 80);
    const entretien = body.entretien ? String(body.entretien).slice(0, 60) : null;
    const ligne = entretien ? `Votre entretien individuel est réservé : ${entretien}.` : "Vous pouvez réserver votre entretien individuel depuis « Mes diagnostics ».";
    try {
      await envoyer(user.email, "Votre invitation — Journées Portes Ouvertes DC-KNOWING",
        page([`Bonjour ${esc(nom)},`, `Vous êtes invité aux <b>Journées Portes Ouvertes DC-KNOWING</b> : ${esc(jour)}, ${esc(horaire)}.`, esc(ligne),
          `Lieu : siège DC-KNOWING, ${esc(ADRESSE)}. L'invitation agenda est jointe.`]),
        `Bonjour ${nom},\n\nVous êtes invité aux Journées Portes Ouvertes DC-KNOWING : ${jour}, ${horaire}.\n${ligne}\nLieu : siège DC-KNOWING, ${ADRESSE}.\nL'invitation agenda est jointe.\n\n${PIED}\n`,
        [{ filename: "JPO-DC-KNOWING.ics", content: ics, content_type: "text/calendar" }]);
      return json(200, { ok: true });
    } catch (e) {
      console.error("invitation", e);
      return json(502, { error: "Envoi impossible pour le moment" });
    }
  }

  // ---------------------------------------------------------------- rapport ou excuse : contrôle de propriété
  const id = String(body.rapport_id ?? "");
  if (!/^[0-9a-f-]{36}$/i.test(id)) return json(400, { error: "rapport_id invalide" });
  const { data: rapport, error: errR } = await admin.from("rapports")
    .select("id, user_id, email, reference, chemin_fichier, formulaire, donnees, score_performance, grade_performance, score_conformite, grade_conformite")
    .eq("id", id).maybeSingle();
  if (errR) return json(500, { error: "Lecture du rapport impossible" });
  if (!rapport || (!serveur && rapport.user_id !== user!.id)) return json(404, { error: "Rapport introuvable" });

  const nom = String((rapport.formulaire as Record<string, unknown>)?.contact_nom ?? "");
  const destinataire = user?.email || await adresseActuelle(rapport.user_id, rapport.email);

  if (type === "rappel") {
    if (!serveur) return json(403, { error: "Réservé au serveur" });
    const jour = String(body.jour_long ?? "").slice(0, 80);
    const accroche = String(body.accroche ?? "").slice(0, 160);
    const programme = (Array.isArray(body.programme) ? body.programme : []).slice(0, 12).map((x) => String(x).slice(0, 120));
    const heure = body.entretien ? String(body.entretien).slice(0, 5) : null;
    const ligne = heure ? `Votre entretien individuel est demain à ${heure}.` : "";
    try {
      await envoyer(destinataire, `Demain, ${jour} — Journées Portes Ouvertes DC-KNOWING`,
        page([`Bonjour ${esc(nom)},`, `<b>${esc(accroche)}</b>`, ...(ligne ? [esc(ligne)] : []), `Le programme :<br>${programme.map(esc).join("<br>")}`,
          `Lieu : siège DC-KNOWING, ${esc(ADRESSE)}.`]),
        `Bonjour ${nom},\n\n${accroche}\n${ligne}\n\n${programme.join("\n")}\n\nLieu : siège DC-KNOWING, ${ADRESSE}.\n\n${PIED}\n`);
      return json(200, { ok: true });
    } catch (e) {
      console.error("rappel", e);
      return json(502, { error: "Envoi impossible pour le moment" });
    }
  }

  if (type === "excuse") {
    const nonReconnu = body.motif === "non_reconnu";
    const raison = nonReconnu
      ? "Aucun montant comptable exploitable n'a été trouvé dans les fichiers déposés. Le diagnostic se calcule de préférence à partir de votre balance générale ; un journal de ventes, d'achats ou de paie permet aussi un premier diagnostic partiel."
      : body.motif === "technique"
        ? "Votre diagnostic n'a pas pu être produit automatiquement à la suite d'un incident technique."
        : "Nous vous prions de nous excuser : les documents déposés n'ont pas pu être lus de façon assez fiable pour produire un rapport automatique.";
    try {
      await envoyer(destinataire, "Votre diagnostic — un conseiller vous rappelle",
        page(["Bonjour,", esc(raison),
          "Plutôt que de vous envoyer des chiffres incertains, un conseiller vous rappelle dans la journée pour reprendre votre diagnostic avec vous.",
          `Si vous êtes encore au salon, passez au stand Check-up 360 (siège DC-KNOWING, ${esc(ADRESSE)}) avec une version numérique de votre balance générale.`,
          "<b>Astuce :</b> les fichiers CSV ou XLSX exportés de votre logiciel comptable sont lus plus vite et plus sûrement qu'un PDF numérisé."],
          undefined, `Référence ${rapport.reference}. Vos fichiers bruts sont supprimés sous 24 h.`),
        `Bonjour,\n\n${raison}\nUn conseiller vous rappelle dans la journée.\n\nRéférence ${rapport.reference}\n\n${PIED}\n`,
        [], true);
      return json(200, { ok: true });
    } catch (e) {
      console.error("excuse", e);
      return json(502, { error: "Envoi impossible pour le moment" });
    }
  }

  // ---------------------------------------------------------------- e-mail du rapport
  if (!rapport.chemin_fichier) return json(409, { error: "Rapport pas encore généré" });
  const nomFichier = `Diagnostic_DC-KNOWING_${rapport.reference}.docx`;
  try {
    const { data: signe, error: errS } = await admin.storage.from("rapports")
      .createSignedUrl(rapport.chemin_fichier, LIEN_VALIDITE_S, { download: nomFichier });
    if (errS || !signe) throw new Error(`URL signée : ${errS?.message}`);

    const pj: PieceJointe[] = [];
    const { data: fichier } = await admin.storage.from("rapports").download(rapport.chemin_fichier);
    if (fichier && fichier.size < PIECE_JOINTE_MAX) {
      pj.push({ filename: nomFichier, content: base64(new Uint8Array(await fichier.arrayBuffer())), content_type: DOCX });
    }

    const resume = ((rapport.donnees as Record<string, unknown>)?.resume ?? {}) as Record<string, unknown>;
    const verdict = String(resume.verdict ?? "");
    const p = `${rapport.score_performance}/100 (${rapport.grade_performance})`;
    const c = `${rapport.score_conformite}/100 (${rapport.grade_conformite})`;
    const espace = SITE ? `<a href="${esc(SITE)}" style="color:#000">« Mes diagnostics »</a>` : "« Mes diagnostics »";
    await envoyer(destinataire, "Votre diagnostic financier — Journées Portes Ouvertes DC-KNOWING",
      page([`Bonjour ${esc(nom)},`, `Votre diagnostic est prêt : <b>performance ${esc(p)}</b> et <b>conformité ${esc(c)}</b>.`,
        `En bref : ${esc(verdict)}`,
        `Il est aussi rangé dans ${espace}, avec votre invitation et le programme. Votre conseiller vous attend pour un entretien individuel.`,
        pj.length ? "Le rapport Word est joint à cet e-mail." : "Téléchargez votre rapport avec le bouton ci-dessous."],
        ["Télécharger mon rapport", signe.signedUrl],
        "Lien sécurisé valable 7 jours. Ce diagnostic ne constitue ni un audit, ni une certification des comptes, ni un avis juridique. Vos fichiers bruts sont supprimés sous 24 h."),
      `Bonjour ${nom},\n\nVotre diagnostic est prêt : performance ${p} et conformité ${c}.\nEn bref : ${verdict}\n\nTélécharger mon rapport (lien sécurisé valable 7 jours) : ${signe.signedUrl}\n\nCe diagnostic ne constitue ni un audit, ni une certification des comptes, ni un avis juridique.\n\n${PIED}\n`,
      pj, true);

    await admin.from("rapports").update({ statut: "envoye", envoye_le: new Date().toISOString(), erreur: null }).eq("id", id);
    return json(200, { ok: true, piece_jointe: pj.length > 0 });
  } catch (e) {
    console.error("rapport", e);
    await admin.from("rapports").update({ statut: "erreur", erreur: `e-mail non envoyé : ${String(e).slice(0, 300)}` }).eq("id", id);
    return json(502, { error: "Envoi de l'e-mail impossible pour le moment" });
  }
});
