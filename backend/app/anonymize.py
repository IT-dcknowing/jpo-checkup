"""Anonymisation du texte avant tout appel IA : noms, NCC, RIB, e-mails, téléphones."""
from __future__ import annotations

import re

from .schemas import DepotForm

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_INTL = re.compile(r"(?<![\d])(?:\+|00)225[\s.-]?(?:\d[\s.-]?){8,10}(?!\d)")
PHONE_LOCAL = re.compile(r"(?<![\d])0[1-9](?:[\s.-]?\d{2}){4}(?!\d)")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s]?[A-Z0-9]{4}){3,7}(?:[\s]?[A-Z0-9]{1,4})?\b")
RIB_CI = re.compile(r"\b[A-Z]{1,2}\d{2,3}[\s-]?\d{5}[\s-]?\d{11,12}[\s-]?\d{2}\b")
NCC = re.compile(r"\b\d{7}[A-Z]\b")


def _word(text: str, needle: str, repl: str) -> str:
    needle = needle.strip()
    if len(needle) < 3:
        return text
    return re.sub(re.escape(needle), repl, text, flags=re.I)


def anonymize(text: str, form: DepotForm) -> str:
    out = text
    out = _word(out, form.raison_sociale, "Client_A")
    if form.ncc:
        out = _word(out, form.ncc, "NCC_X")
    out = _word(out, form.contact_nom, "Contact_X")
    for part in form.contact_nom.split():  # prénom ou nom seul
        if len(part) >= 4:
            out = re.sub(rf"\b{re.escape(part)}\b", "Contact_X", out, flags=re.I)
    out = _word(out, form.contact_email, "[EMAIL]")
    out = _word(out, form.contact_telephone, "[TEL]")
    for rx, repl in ((EMAIL, "[EMAIL]"), (PHONE_INTL, "[TEL]"), (PHONE_LOCAL, "[TEL]"),
                     (RIB_CI, "Compte_X"), (IBAN, "Compte_X"), (NCC, "NCC_X")):
        out = rx.sub(repl, out)
    return out
