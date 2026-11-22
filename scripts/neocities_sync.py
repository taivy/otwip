#!/usr/bin/env python3
"""Two-way sync between the `site/` folder of this repo and a Neocities site.

Three-way merge per file, using `.neocities-state.json` (the sha1 of every
file as of the last successful sync) as the common ancestor:

  repo == neocities             -> in sync, nothing to do
  repo unchanged since last sync -> Neocities changed: download (or delete locally)
  Neocities unchanged            -> repo changed: upload (or delete on Neocities)
  both changed                   -> conflict: Neocities wins, the repo version
                                    stays in git history. If Neocities deleted a
                                    file the repo edited, the repo version is
                                    re-uploaded so nothing is lost.

Env: NEOCITIES_API_KEY (required), SITE_DIR (default "site"), DRY_RUN=1.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

API = "https://neocities.org/api"
STATE_FILE = Path(".neocities-state.json")
SITE_DIR = Path(os.environ.get("SITE_DIR", "site"))
UPLOAD_BATCH = 20


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# --------------------------------------------------------------------------- planning

def plan(local: dict, remote: dict, state: dict) -> list[tuple[str, str]]:
    """Return a list of (action, path). Actions: download, upload,
    delete_local, delete_remote, conflict_download, conflict_upload."""
    actions = []
    for p in sorted(set(local) | set(remote) | set(state)):
        L, R, S = local.get(p), remote.get(p), state.get(p)
        if L == R:
            continue
        if L == S:  # only Neocities changed
            actions.append(("delete_local" if R is None else "download", p))
        elif R == S:  # only the repo changed
            actions.append(("delete_remote" if L is None else "upload", p))
        else:  # both changed
            actions.append(("conflict_upload" if R is None else "conflict_download", p))
    return actions


# --------------------------------------------------------------------------- I/O

class Neocities:
    def __init__(self, api_key: str):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {api_key}"
        info = self._check(self.s.get(f"{API}/info", timeout=30))
        self.sitename = info["info"]["sitename"]
        self.base = f"https://{self.sitename}.neocities.org"

    @staticmethod
    def _check(r: requests.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            r.raise_for_status()
            raise
        if r.status_code >= 400 or body.get("result") != "success":
            raise RuntimeError(f"Neocities API error {r.status_code}: {body}")
        return body

    def list(self) -> dict:
        body = self._check(self.s.get(f"{API}/list", timeout=60))
        return {f["path"]: f["sha1"] for f in body["files"] if not f.get("is_directory")}

    def download(self, path: str, expected_sha: str) -> bytes | None:
        url = f"{self.base}/{quote(path)}"
        for attempt in range(4):
            r = self.s.get(url, params={"_sync": int(time.time())}, timeout=60,
                           headers={"Cache-Control": "no-cache"})
            if r.ok and sha1(r.content) == expected_sha:
                return r.content
            time.sleep(3 * (attempt + 1))  # CDN may still serve an older copy
        return None

    def upload(self, files: dict[str, bytes]) -> None:
        items = list(files.items())
        for i in range(0, len(items), UPLOAD_BATCH):
            batch = items[i:i + UPLOAD_BATCH]
            self._check(self.s.post(f"{API}/upload", timeout=300,
                                    files=[(p, (Path(p).name, d)) for p, d in batch]))

    def delete(self, paths: list[str]) -> None:
        if paths:
            self._check(self.s.post(f"{API}/delete", timeout=60,
                                    data=[("filenames[]", p) for p in paths]))


def scan_local(root: Path) -> dict:
    out = {}
    if root.exists():
        for f in root.rglob("*"):
            if f.is_file() and not any(part.startswith(".git") for part in f.parts):
                out[f.relative_to(root).as_posix()] = sha1(f.read_bytes())
    return out


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(dict(sorted(state.items())), indent=1) + "\n")


# --------------------------------------------------------------------------- run

def sync(client, root: Path = SITE_DIR, dry_run: bool = False) -> dict:
    local, remote, state = scan_local(root), client.list(), load_state()
    actions = plan(local, remote, state)
    for a, p in actions:
        print(f"{a:18} {p}")
    if not actions:
        print("Already in sync.")
    if dry_run:
        return {"actions": actions}

    new_state = {p: s for p, s in local.items() if remote.get(p) == s}
    failed = []

    uploads = {p: (root / p).read_bytes() for a, p in actions if a in ("upload", "conflict_upload")}
    if uploads:
        client.upload(uploads)
        new_state.update({p: sha1(d) for p, d in uploads.items()})

    deletes = [p for a, p in actions if a == "delete_remote"]
    if deletes:
        client.delete(deletes)
        for p in deletes:
            new_state.pop(p, None)

    for a, p in actions:
        if a in ("download", "conflict_download"):
            data = client.download(p, remote[p])
            if data is None:
                failed.append(p)
                if p in state:
                    new_state[p] = state[p]  # keep ancestor, retry next run
                continue
            dest = root / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            new_state[p] = remote[p]
        elif a == "delete_local":
            (root / p).unlink(missing_ok=True)
            new_state.pop(p, None)

    # tidy empty folders left after local deletes
    if root.exists():
        for d in sorted((x for x in root.rglob("*") if x.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()

    save_state(new_state)
    if failed:
        print(f"WARNING: could not fetch fresh copies of {failed}; will retry next run.")
    return {"actions": actions, "failed": failed}


def main() -> int:
    key = os.environ.get("NEOCITIES_API_KEY")
    if not key:
        print("NEOCITIES_API_KEY is not set", file=sys.stderr)
        return 1
    sync(Neocities(key), dry_run=os.environ.get("DRY_RUN") == "1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
