// Fonction Vercel (Node) : Markdown balisé du rapport → document Word (.docx).
// Appelée uniquement par l'API Python (même projet), avec le secret partagé SECRET_KEY dans l'en-tête X-Render-Secret.
const crypto = require("crypto");
const { convertir } = require("../word/md_vers_word.js");

const MAX = 2 * 1024 * 1024; // 2 Mo de Markdown au plus

function lireCorps(req) {
  if (typeof req.body === "string") return Promise.resolve(req.body);
  if (Buffer.isBuffer(req.body)) return Promise.resolve(req.body.toString("utf8"));
  return new Promise((resolve, reject) => {
    const parts = [];
    let n = 0;
    req.on("data", (c) => { n += c.length; if (n > MAX) { reject(new Error("trop volumineux")); req.destroy(); } else parts.push(c); });
    req.on("end", () => resolve(Buffer.concat(parts).toString("utf8")));
    req.on("error", reject);
  });
}

function egal(a, b) {
  const x = Buffer.from(String(a || "")), y = Buffer.from(String(b || ""));
  return x.length === y.length && x.length > 0 && crypto.timingSafeEqual(x, y);
}

module.exports = async (req, res) => {
  if (req.method !== "POST") { res.statusCode = 405; return res.end("Méthode non autorisée"); }
  if (!egal(req.headers["x-render-secret"], process.env.SECRET_KEY)) { res.statusCode = 401; return res.end("Non autorisé"); }
  try {
    const md = await lireCorps(req);
    if (!md || md.length > MAX) { res.statusCode = 413; return res.end("Markdown absent ou trop volumineux"); }
    const buf = await convertir(md);
    res.statusCode = 200;
    res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    res.setHeader("Cache-Control", "no-store");
    return res.end(buf);
  } catch (e) {
    console.error("rendu Word en échec", e);
    res.statusCode = 500;
    return res.end("Rendu Word en échec");
  }
};

module.exports.config = { api: { bodyParser: false } };
