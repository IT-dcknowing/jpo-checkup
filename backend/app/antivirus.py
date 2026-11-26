"""Analyse des fichiers déposés avant toute lecture.

1. Contenu actif (toujours) : PDF avec JavaScript ou action de lancement, classeur Excel avec macros (vbaProject.bin)
   ou objets incorporés (OLE). Ces fichiers ne sont pas des états comptables ordinaires : refusés.
2. Antivirus ClamAV (si CLAMAV_HOST est défini) : flux INSTREAM vers un démon clamd (Vercel n'en héberge pas ; voir
   DEPLOIEMENT.md §10). CLAMAV_OBLIGATOIRE=1 : un antivirus injoignable bloque le traitement au lieu de le laisser passer.
"""
from __future__ import annotations

import io
import logging
import re
import socket
import struct
import zipfile

log = logging.getLogger("jpo.antivirus")

PDF_ACTIF = re.compile(rb"/(JavaScript|JS|Launch|RichMedia)\b")


class FichierRefuse(Exception):
    """Fichier dangereux ou analyse antivirus impossible (mode obligatoire)."""


def contenu_actif(data: bytes, ext: str) -> str | None:
    """Motif du refus, ou None si rien d'actif n'est repéré."""
    if ext == ".pdf":
        m = PDF_ACTIF.search(data)
        if m:
            return f"PDF contenant du code ou une action automatique (/{m.group(1).decode()})"
    elif ext == ".xlsx":
        try:
            noms = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return None  # le lecteur dira que ce n'est pas un vrai classeur
        if any(n.lower().endswith("vbaproject.bin") for n in noms):
            return "classeur contenant des macros"
        if any(n.lower().startswith("xl/embeddings/") for n in noms):
            return "classeur contenant des objets incorporés"
    return None


def clamd_scan(data: bytes, host: str, port: int = 3310, timeout: float = 30) -> str | None:
    """Nom du virus détecté par clamd, ou None si le fichier est sain. Lève OSError si clamd ne répond pas."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(b"zINSTREAM\0")
        for i in range(0, len(data), 64 * 1024):
            bloc = data[i:i + 64 * 1024]
            s.sendall(struct.pack(">I", len(bloc)) + bloc)
        s.sendall(struct.pack(">I", 0))
        reponse = b""
        while not reponse.endswith(b"\0"):
            part = s.recv(4096)
            if not part:
                break
            reponse += part
    texte = reponse.rstrip(b"\0").decode(errors="replace")
    if texte.endswith("FOUND"):
        return texte.split(":", 1)[-1].rsplit(" ", 1)[0].strip()
    if not texte.endswith("OK"):
        raise OSError(f"réponse clamd inattendue : {texte[:120]}")
    return None


def verifier(data: bytes, nom: str, ext: str, clamav_host: str = "", clamav_port: int = 3310, obligatoire: bool = False) -> None:
    motif = contenu_actif(data, ext)
    if motif:
        raise FichierRefuse(f"« {nom} » refusé : {motif}")
    if not clamav_host:
        return
    try:
        virus = clamd_scan(data, clamav_host, clamav_port)
    except OSError as e:
        log.error("antivirus injoignable : %s", e)
        if obligatoire:
            raise FichierRefuse("analyse antivirus indisponible : traitement suspendu") from e
        return
    if virus:
        raise FichierRefuse(f"« {nom} » refusé : menace détectée ({virus})")
