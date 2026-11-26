"""Supabase en mémoire pour les tests : sous-ensemble de PostgREST, Storage, Auth et Edge Functions utilisé par l'app."""
from __future__ import annotations

import copy
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone

from app.supa import SupabaseError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _match(row: dict, key: str, cond: str) -> bool:
    val = row.get(key)
    neg = cond.startswith("not.")
    if neg:
        cond = cond[4:]
    op, _, arg = cond.partition(".")
    if op == "eq":
        ok = str(val).lower() == arg.lower() if isinstance(val, bool) else str(val) == arg
    elif op == "in":
        ok = str(val) in arg.strip("()").split(",")
    elif op == "gte":
        ok = val is not None and str(val) >= arg
    elif op == "lt":
        ok = val is not None and str(val) < arg
    else:
        raise NotImplementedError(op)
    return ok != neg


class FakeSupabase:
    RESERVED = {"select", "order", "limit"}

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {k: [] for k in ("profiles", "rapports", "documents", "entretiens", "audit_logs", "compteurs",
                                                              "quotas")}
        self.storage: dict[str, dict[str, bytes]] = {"documents": {}, "rapports": {}}
        self.users: dict[str, dict] = {}       # jeton -> utilisateur
        self.invocations: list[tuple[str, dict, str | None]] = []
        self.email_fails = False

    # ------------------------------------------------------------------ comptes de test
    def add_user(self, email: str) -> str:
        uid = str(uuid.uuid4())
        token = f"jeton-{uid}"
        self.users[token] = {"id": uid, "email": email}
        self.tables["profiles"].append({"id": uid, "email": email, "nom": None, "entreprise": None, "telephone": None, "created_at": _now()})
        return token

    def user_id(self, token: str) -> str:
        return self.users[token]["id"]

    # ------------------------------------------------------------------ PostgREST
    def _rows(self, table: str, params: dict) -> list[dict]:
        rows = [r for r in self.tables[table] if all(_match(r, k, v) for k, v in params.items() if k not in self.RESERVED)]
        if "order" in params:
            col, _, sens = params["order"].partition(".")
            rows.sort(key=lambda r: str(r.get(col) or ""), reverse=sens == "desc")
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        return rows

    def select(self, table, params=None):
        return copy.deepcopy(self._rows(table, params or {}))

    def select_one(self, table, params):
        rows = self.select(table, {**params, "limit": "1"})
        return rows[0] if rows else None

    def insert(self, table, row):
        rows = row if isinstance(row, list) else [row]
        out = []
        for r in rows:
            r = dict(r)
            if table == "rapports":
                r = {"statut": "en_attente", "etape": "EN_ATTENTE", "progression": 5, "donnees": {}, "journal": [], "score_performance": None,
                     "grade_performance": None, "score_conformite": None, "grade_conformite": None, "couverture_pct": None,
                     "chemin_fichier": None, "chemin_markdown": None, "pages": None, "lecture_partielle": False, "modele_ia": None,
                     "erreur": None, "erreur_publique": None, "rappel_demande": False, "rappel_effectue": False, "entretien_realise": False,
                     "traitement_depuis": None, "tentatives": 0, "termine_le": None, "envoye_le": None, **r}
            if table == "documents":
                r.setdefault("rapport_id", None)
                r.setdefault("categorie", "autre")
            if table == "audit_logs":
                r.setdefault("horodatage", _now())
            else:
                r.setdefault("id", str(uuid.uuid4()))
            r.setdefault("created_at", _now())
            self.tables[table].append(r)
            out.append(copy.deepcopy(r))
        return out

    def update(self, table, filters, values):
        rows = self._rows(table, filters)
        for r in rows:
            r.update(copy.deepcopy(values))
        return copy.deepcopy(rows)

    def delete(self, table, filters):
        rows = self._rows(table, filters)
        self.tables[table] = [r for r in self.tables[table] if r not in rows]
        if table == "rapports":  # cascades
            ids = {r["id"] for r in rows}
            self.tables["documents"] = [d for d in self.tables["documents"] if d["rapport_id"] not in ids]
            self.tables["entretiens"] = [e for e in self.tables["entretiens"] if e["rapport_id"] not in ids]
        return copy.deepcopy(rows)

    def rpc(self, fn, args):
        if fn == "creer_rapport":
            uid = args["p_user"]
            depuis = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            if sum(1 for r in self.tables["rapports"] if r["user_id"] == uid and r["created_at"] >= depuis) >= args["p_limite_heure"]:
                raise SupabaseError(400, "QUOTA_DEPOTS")
            libres = [d for d in self.tables["documents"] if d["id"] in args["p_documents"] and d["user_id"] == uid and not d["rapport_id"]]
            if len(libres) != len(args["p_documents"]):
                raise SupabaseError(400, "DOCUMENTS_INDISPONIBLES")
            ref = self.rpc("prochaine_reference", {"p_annee": str(args["p_jour"])[:4]})
            row = self.insert("rapports", {"user_id": uid, "email": args["p_email"], "reference": ref,
                                           "titre": f"Diagnostic {ref} — {args['p_titre_entreprise']}"[:200],
                                           "jour_evenement": args["p_jour"], "secteur": args["p_secteur"], "formulaire": args["p_formulaire"]})[0]
            for d in libres:
                d["rapport_id"] = row["id"]
                d["categorie"] = args["p_categories"].get(d["id"], d["categorie"])
            return [row]
        if fn == "fusionner_donnees":
            r = next(r for r in self.tables["rapports"] if r["id"] == args["p_id"])
            r["donnees"] = {**r["donnees"], **copy.deepcopy(args["p_patch"])}
            return None
        if fn == "consommer_quota":
            now = time.time()
            total = sum(q["poids"] for q in self.tables["quotas"] if q["cle"] == args["p_cle"] and q["t"] > now - args["p_fenetre_s"])
            if total + args["p_poids"] > args["p_max"]:
                return False
            self.tables["quotas"].append({"cle": args["p_cle"], "poids": args["p_poids"], "t": now, "created_at": _now()})
            return True
        if fn == "purger_quotas":
            self.tables["quotas"] = [q for q in self.tables["quotas"] if q["t"] > time.time() - 2 * 86400]
            return None
        if fn == "prochaine_reference":
            c = next((c for c in self.tables["compteurs"] if c["annee"] == args["p_annee"]), None)
            if not c:
                c = {"annee": args["p_annee"], "n": 0}
                self.tables["compteurs"].append(c)
            c["n"] += 1
            return f"JPO-{args['p_annee']}-{c['n']:04d}"
        if fn == "journaliser":
            r = next(r for r in self.tables["rapports"] if r["id"] == args["p_id"])
            et = args["p_etape"]
            r.update(etape=et, progression=args["p_progression"], journal=r["journal"] + [args["p_evenement"]],
                     statut={"ERREUR": "erreur", "ENVOYE": "envoye", "TERMINE": "genere"}.get(et, "en_attente"))
            return None
        if fn == "prendre_traitement":
            r = next((r for r in self.tables["rapports"] if r["id"] == args["p_id"]), None)
            if not r or r["etape"] in ("TERMINE", "ENVOYE", "ERREUR"):
                return False
            limite = (datetime.now(UTC) - timedelta(seconds=args["p_bail_secondes"])).isoformat()
            if r["traitement_depuis"] and r["traitement_depuis"] >= limite:
                return False
            r["traitement_depuis"] = _now()
            r["tentatives"] = r.get("tentatives", 0) + 1
            return True
        if fn == "reserver_entretien":
            n = sum(1 for e in self.tables["entretiens"] if e["jour"] == args["p_jour"] and e["heure"] == args["p_heure"]
                    and e["rapport_id"] != args["p_rapport"])
            if n >= args["p_capacite"]:
                return False
            self.tables["entretiens"] = [e for e in self.tables["entretiens"] if e["rapport_id"] != args["p_rapport"]]
            self.tables["entretiens"].append({"rapport_id": args["p_rapport"], "user_id": args["p_user"], "jour": args["p_jour"],
                                              "heure": args["p_heure"], "created_at": _now()})
            return True
        if fn == "creneaux_pris":
            cpt: dict[str, int] = {}
            for e in self.tables["entretiens"]:
                if e["jour"] == args["p_jour"] and e["user_id"] != args.get("p_exclure"):
                    cpt[e["heure"]] = cpt.get(e["heure"], 0) + 1
            return sorted(h for h, n in cpt.items() if n >= args["p_capacite"])
        raise NotImplementedError(fn)

    # ------------------------------------------------------------------ stockage
    def download(self, bucket, path):
        if path not in self.storage[bucket]:
            raise SupabaseError(404, "Object not found")
        return self.storage[bucket][path]

    def upload(self, bucket, path, data, content_type):
        self.storage[bucket][path] = data

    def remove(self, bucket, paths):
        for p in paths:
            self.storage[bucket].pop(p, None)

    def signed_upload(self, bucket, path):
        return f"jeton-envoi:{bucket}/{path}"

    def object_size(self, bucket, path):
        data = self.storage[bucket].get(path)
        return None if data is None else len(data)

    def signed_url(self, bucket, path, expires_in, download_name=None):
        return f"https://fake.supabase.co/storage/v1/object/sign/{bucket}/{path}?token=signe&expires={expires_in}"

    # ------------------------------------------------------------------ auth
    def forget_user(self, token):
        return None

    def get_user(self, token):
        return copy.deepcopy(self.users.get(token))

    def delete_user(self, user_id):
        self.users = {t: u for t, u in self.users.items() if u["id"] != user_id}
        for table in ("profiles",):
            self.tables[table] = [r for r in self.tables[table] if r["id"] != user_id]
        self.tables["documents"] = [d for d in self.tables["documents"] if d["user_id"] != user_id]

    # ------------------------------------------------------------------ Edge Function envoyer-rapport (simulée)
    def invoke(self, fn, body, token=None, timeout=60):
        self.invocations.append((fn, body, token))
        if body.get("type") in (None, "rapport"):
            r = next(r for r in self.tables["rapports"] if r["id"] == body["rapport_id"])
            if token is not None and self.users.get(token, {}).get("id") != r["user_id"]:
                raise SupabaseError(404, "Rapport introuvable")
            if self.email_fails:
                r.update(statut="erreur", erreur="e-mail non envoyé")
                raise SupabaseError(502, "Envoi de l'e-mail impossible")
            r.update(statut="envoye", envoye_le=_now(), erreur=None)
        return {"ok": True}
