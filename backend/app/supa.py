"""Client Supabase minimal (REST, Storage, Auth) pour le serveur — httpx, sans dépendance supplémentaire.

La clé service_role (SUPABASE_SERVICE_ROLE_KEY) ne quitte jamais le serveur : elle n'est utilisée qu'ici.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx


class SupabaseError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Supabase HTTP {status} : {message}")
        self.status = status


class Supabase:
    def __init__(self, url: str, service_key: str, anon_key: str = "", http: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.service_key = service_key
        self.anon_key = anon_key or service_key
        self.http = http or httpx.Client(timeout=30)
        self._users: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------ utilitaires
    def _headers(self, extra: dict | None = None) -> dict:
        h = {"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}"}
        return h | (extra or {})

    @staticmethod
    def _check(r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("message") or body.get("msg") or body.get("error") or r.text
            except ValueError:
                msg = r.text
            raise SupabaseError(r.status_code, str(msg)[:400])
        return r

    # ------------------------------------------------------------------ base de données (PostgREST)
    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        r = self.http.get(f"{self.url}/rest/v1/{table}", params=params or {}, headers=self._headers())
        return self._check(r).json()

    def select_one(self, table: str, params: dict[str, str]) -> dict | None:
        rows = self.select(table, {**params, "limit": "1"})
        return rows[0] if rows else None

    def insert(self, table: str, row: dict | list[dict]) -> list[dict]:
        r = self.http.post(f"{self.url}/rest/v1/{table}", json=row, headers=self._headers({"Prefer": "return=representation"}))
        return self._check(r).json()

    def update(self, table: str, filters: dict[str, str], values: dict) -> list[dict]:
        r = self.http.patch(f"{self.url}/rest/v1/{table}", params=filters, json=values,
                            headers=self._headers({"Prefer": "return=representation"}))
        return self._check(r).json()

    def delete(self, table: str, filters: dict[str, str]) -> list[dict]:
        r = self.http.delete(f"{self.url}/rest/v1/{table}", params=filters, headers=self._headers({"Prefer": "return=representation"}))
        return self._check(r).json()

    def rpc(self, fn: str, args: dict) -> Any:
        r = self.http.post(f"{self.url}/rest/v1/rpc/{fn}", json=args, headers=self._headers())
        self._check(r)
        return r.json() if r.content else None

    # ------------------------------------------------------------------ stockage
    def _obj(self, bucket: str, path: str) -> str:
        return f"{quote(bucket)}/{quote(path)}"

    def download(self, bucket: str, path: str) -> bytes:
        r = self.http.get(f"{self.url}/storage/v1/object/{self._obj(bucket, path)}", headers=self._headers())
        return self._check(r).content

    def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        r = self.http.post(f"{self.url}/storage/v1/object/{self._obj(bucket, path)}", content=data,
                           headers=self._headers({"Content-Type": content_type, "x-upsert": "true"}))
        self._check(r)

    def remove(self, bucket: str, paths: list[str]) -> None:
        if not paths:
            return
        r = self.http.request("DELETE", f"{self.url}/storage/v1/object/{quote(bucket)}", json={"prefixes": paths}, headers=self._headers())
        self._check(r)

    def signed_url(self, bucket: str, path: str, expires_in: int, download_name: str | None = None) -> str:
        r = self.http.post(f"{self.url}/storage/v1/object/sign/{self._obj(bucket, path)}", json={"expiresIn": expires_in},
                           headers=self._headers())
        signed = self._check(r).json()["signedURL"]
        url = f"{self.url}/storage/v1{signed}"
        if download_name:
            url += f"&download={quote(download_name)}"
        return url

    # ------------------------------------------------------------------ authentification
    def get_user(self, access_token: str) -> dict | None:
        """Vérifie un jeton de session Supabase (JWT) auprès de GoTrue ; None si invalide ou expiré. Cache de 60 s."""
        if not access_token or len(access_token) > 4096:
            return None
        now = time.time()
        hit = self._users.get(access_token)
        if hit and hit[0] > now:
            return hit[1]
        r = self.http.get(f"{self.url}/auth/v1/user", headers={"apikey": self.anon_key, "Authorization": f"Bearer {access_token}"})
        if r.status_code != 200:
            return None
        user = r.json()
        if len(self._users) > 500:
            self._users.clear()
        self._users[access_token] = (now + 60, user)
        return user

    def delete_user(self, user_id: str) -> None:
        r = self.http.delete(f"{self.url}/auth/v1/admin/users/{quote(user_id)}", headers=self._headers())
        self._check(r)

    # ------------------------------------------------------------------ fonctions Edge
    def invoke(self, fn: str, body: dict, token: str | None = None, timeout: float = 60) -> dict:
        """Appelle une Edge Function (jeton du visiteur, sinon service_role)."""
        tok = token or self.service_key
        r = self.http.post(f"{self.url}/functions/v1/{fn}", json=body, timeout=timeout,
                           headers={"apikey": self.anon_key, "Authorization": f"Bearer {tok}"})
        self._check(r)
        return r.json() if r.content else {}
