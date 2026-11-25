"""E-mails (SMTP) : code de connexion, rapport, excuse en cas de lecture impossible, invitation."""
from __future__ import annotations

import html
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from .config import Settings
from .textes import ADRESSE, CONTACT_MAIL, SITE, TELEPHONES

SUBJECT_REPORT = "Votre diagnostic financier — Journées Portes Ouvertes DC-KNOWING"
SUBJECT_ERROR = "Votre diagnostic — un conseiller vous rappelle"
SUBJECT_INVITATION = "Votre invitation — Journées Portes Ouvertes DC-KNOWING"
FOOT = f"{TELEPHONES} · {SITE} · {CONTACT_MAIL} · {ADRESSE}"


def configured(s: Settings) -> bool:
    return bool(s.smtp_host)


def _send(s: Settings, msg: EmailMessage) -> None:
    if s.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
    with server:
        if s.smtp_security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if s.smtp_user:
            server.login(s.smtp_user, s.smtp_password)
        server.send_message(msg)


def _page(titre_html: str, corps: list[str], bouton: tuple[str, str] | None = None, pied: str = "") -> str:
    """Gabarit HTML sobre (charte noir et or), styles en ligne pour les messageries."""
    paras = "".join(f'<p style="margin:0 0 12px;font-size:15px;line-height:1.55">{p}</p>' for p in corps)
    btn = (f'<p style="margin:18px 0"><a href="{html.escape(bouton[1])}" style="display:inline-block;background:#FFD700;color:#000;'
           f'padding:12px 22px;border-radius:12px;font-weight:700;text-decoration:none">{html.escape(bouton[0])}</a></p>') if bouton else ""
    return (f'<div style="font-family:Arial,Helvetica,sans-serif;color:#000;max-width:560px;margin:0 auto;padding:24px;border:1px solid #E0E0E0;border-radius:16px">'
            f'<p style="margin:0 0 18px;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase">DC-KNOWING · Journées Portes Ouvertes</p>'
            f'{titre_html}{paras}{btn}'
            f'<p style="margin:18px 0 0;padding-top:12px;border-top:1px solid #E0E0E0;font-size:11px;color:#525252">{html.escape(pied)}<br>{html.escape(FOOT)}</p></div>')


def _msg(s: Settings, to: str, subject: str, text: str, html_body: str, cc: bool = False) -> EmailMessage:
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, f"DC-KNOWING · Diagnostic JPO <{s.mail_from}>", to
    if cc and s.mail_copy_to:
        m["Cc"] = ", ".join(s.mail_copy_to)
    m.set_content(text)
    m.add_alternative(html_body, subtype="html")
    return m


def _attach(msg: EmailMessage, path: Path | None, mime: tuple[str, str]) -> None:
    if path and path.exists():
        msg.add_attachment(path.read_bytes(), maintype=mime[0], subtype=mime[1], filename=path.name)


def send_code(s: Settings, to: str, code: str) -> None:
    text = (f"Bonjour,\n\nVoici votre code pour accéder à « Mes diagnostics » et lancer votre Check-up 360 : {code}\n\n"
            "Il est valable 10 minutes et ne sert qu'une fois. Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail.\n"
            f"DC-KNOWING ne vous demandera jamais ce code par téléphone.\n\n{FOOT}\n")
    body = _page("", ["Bonjour,", "Voici votre code pour accéder à « Mes diagnostics » et lancer votre Check-up 360 :",
                      f'<span style="display:inline-block;padding:14px 26px;border-radius:14px;background:#FFD700;border:1.5px solid #000;'
                      f'font-family:Consolas,monospace;font-size:32px;font-weight:700;letter-spacing:.3em">{html.escape(code)}</span>',
                      "Il est valable 10 minutes et ne sert qu'une fois. Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail."],
                 pied="DC-KNOWING ne vous demandera jamais ce code par téléphone.")
    _send(s, _msg(s, to, f"Votre code d'accès : {code}", text, body))


def send_report(s: Settings, to: str, nom: str, faits: dict, verdict: str, docx: Path | None, pdf: Path | None, link: str) -> None:
    p, c = f"{faits['score_performance']}/100 ({faits['grade_performance']})", f"{faits['score_conformite']}/100 ({faits['grade_conformite']})"
    text = (f"Bonjour {nom},\n\nVotre diagnostic est prêt : performance {p} et conformité {c}.\nEn bref : {verdict}\n\n"
            "Il est aussi rangé dans « Mes diagnostics », avec votre invitation et le programme. Votre conseiller vous attend pour un entretien individuel.\n\n"
            f"Télécharger mon rapport (lien sécurisé valable 7 jours) : {link}\n\n"
            "Ce diagnostic ne constitue ni un audit, ni une certification des comptes, ni un avis juridique. Vos fichiers bruts sont supprimés sous 24 h.\n\n"
            f"{FOOT}\n")
    body = _page("", [f"Bonjour {html.escape(nom)},", f"Votre diagnostic est prêt : <b>performance {p}</b> et <b>conformité {c}</b>.",
                      f"En bref : {html.escape(verdict)}",
                      "Il est aussi rangé dans « Mes diagnostics », avec votre invitation et le programme. Votre conseiller vous attend pour un entretien individuel."],
                 ("Télécharger mon rapport", link),
                 "Lien sécurisé valable 7 jours. Ce diagnostic ne constitue ni un audit, ni une certification des comptes, ni un avis juridique. Vos fichiers bruts sont supprimés sous 24 h.")
    m = _msg(s, to, SUBJECT_REPORT, text, body, cc=True)
    _attach(m, docx, ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"))
    _attach(m, pdf, ("application", "pdf"))
    _send(s, m)


def send_apology(s: Settings, to: str, ref: str) -> None:
    text = ("Bonjour,\n\nNous vous prions de nous excuser : les documents déposés n'ont pas pu être lus de façon assez fiable pour produire un rapport automatique.\n\n"
            "Plutôt que de vous envoyer des chiffres incertains, un conseiller vous rappelle dans la journée pour reprendre votre diagnostic avec vous.\n"
            f"Si vous êtes encore au salon, passez au stand Check-up 360 (siège DC-KNOWING, {ADRESSE}) avec une version numérique de votre balance générale.\n\n"
            f"Référence {ref}\nAstuce : les fichiers CSV ou XLSX exportés de votre logiciel comptable sont lus plus vite et plus sûrement qu'un PDF numérisé.\n\n{FOOT}\n")
    body = _page("", ["Bonjour,", "Nous vous prions de nous excuser : les documents déposés n'ont pas pu être lus de façon assez fiable pour produire un rapport automatique.",
                      "Plutôt que de vous envoyer des chiffres incertains, un conseiller vous rappelle dans la journée pour reprendre votre diagnostic avec vous.",
                      f"Si vous êtes encore au salon, passez au stand Check-up 360 (siège DC-KNOWING, {html.escape(ADRESSE)}) avec une version numérique de votre balance générale.",
                      f"<b>Astuce :</b> les fichiers CSV ou XLSX exportés de votre logiciel comptable sont lus plus vite et plus sûrement qu'un PDF numérisé."],
                 pied=f"Référence {ref}. Vos fichiers bruts sont supprimés sous 24 h.")
    _send(s, _msg(s, to, SUBJECT_ERROR, text, body, cc=True))


def send_invitation(s: Settings, to: str, nom: str, jour_long: str, horaire: str, entretien: str | None, ics: bytes) -> None:
    ligne = f"Votre entretien individuel est réservé à {entretien}." if entretien else "Vous pouvez réserver votre entretien individuel depuis « Mes diagnostics »."
    text = (f"Bonjour {nom},\n\nVous êtes invité aux Journées Portes Ouvertes DC-KNOWING : {jour_long}, {horaire}.\n{ligne}\n"
            f"Lieu : siège DC-KNOWING, {ADRESSE}.\nL'invitation agenda est jointe.\n\n{FOOT}\n")
    body = _page("", [f"Bonjour {html.escape(nom)},", f"Vous êtes invité aux <b>Journées Portes Ouvertes DC-KNOWING</b> : {html.escape(jour_long)}, {html.escape(horaire)}.",
                      html.escape(ligne), f"Lieu : siège DC-KNOWING, {html.escape(ADRESSE)}. L'invitation agenda est jointe."])
    m = _msg(s, to, SUBJECT_INVITATION, text, body)
    m.add_attachment(ics, maintype="text", subtype="calendar", filename="JPO-DC-KNOWING.ics")
    _send(s, m)
