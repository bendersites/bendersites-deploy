#!/usr/bin/env python3
"""
BenderSites — Auto Deploy (Railway)
Pollt Trello, lädt Demos von R2, deployed zu Cloudflare Pages via Wrangler.
"""

import os
import re
import sys
import time
import shutil
import subprocess
import tempfile
import requests
import boto3
from pathlib import Path

TRELLO_KEY    = os.environ.get("TRELLO_KEY")
TRELLO_TOKEN  = os.environ.get("TRELLO_TOKEN")
TRELLO_BOARD  = os.environ.get("TRELLO_BOARD", "Chantal - Berlin")
INTEREST_LIST = os.environ.get("INTEREST_LIST", "Interesse")

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID")
CF_API_TOKEN  = os.environ.get("CF_API_TOKEN")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY")
R2_BUCKET     = os.environ.get("R2_BUCKET", "bendersites-demos")

POLL_INTERVAL = 60
TRELLO_BASE   = "https://api.trello.com/1"
processed     = set()

# ── R2 ────────────────────────────────────────────────────────

def get_s3():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        region_name="auto"
    )

def download_from_r2(customer_name, demo, tmp_dir):
    s3 = get_s3()
    prefix = f"{customer_name}/{demo}/"
    response = s3.list_objects_v2(Bucket=R2_BUCKET, Prefix=prefix)

    if "Contents" not in response:
        return False

    dest = Path(tmp_dir) / demo
    dest.mkdir(parents=True, exist_ok=True)

    for obj in response["Contents"]:
        key = obj["Key"]
        rel = key[len(prefix):]
        if not rel:
            continue
        local = dest / rel
        local.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(R2_BUCKET, key, str(local))

    return True

# ── Trello ────────────────────────────────────────────────────

def trello_get(path, **params):
    params.update(key=TRELLO_KEY, token=TRELLO_TOKEN)
    r = requests.get(f"{TRELLO_BASE}{path}", params=params)
    r.raise_for_status()
    return r.json()

def trello_post(path, **data):
    data.update(key=TRELLO_KEY, token=TRELLO_TOKEN)
    r = requests.post(f"{TRELLO_BASE}{path}", json=data)
    r.raise_for_status()
    return r.json()

def add_comment(card_id, text):
    trello_post(f"/cards/{card_id}/actions/comments", text=text)

# ── Slug + Deploy ─────────────────────────────────────────────

def slugify(name):
    name = name.lower()
    for a, b in [("ä","ae"),("ö","oe"),("ü","ue"),("ß","ss")]:
        name = name.replace(a, b)
    name = re.sub(r'[^a-z0-9]+', '-', name)
    return name.strip('-')[:50]

def ensure_pages_project(project_name, env):
    check = subprocess.run(
        ["wrangler", "pages", "project", "list"],
        capture_output=True, text=True, env=env
    )
    if project_name in check.stdout:
        return
    print(f"  → Erstelle Projekt '{project_name}'...")
    result = subprocess.run(
        ["wrangler", "pages", "project", "create", project_name, "--production-branch", "main"],
        capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        raise Exception(result.stderr.strip() or result.stdout.strip())
    time.sleep(2)

def wrangler_deploy(project_name, folder_path, env):
    ensure_pages_project(project_name, env)
    print(f"  → Deploy '{project_name}'...")
    result = subprocess.run(
        [
            "wrangler", "pages", "deploy", str(folder_path),
            "--project-name", project_name,
            "--branch", "main",
            "--commit-dirty=true"
        ],
        capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        raise Exception(result.stderr.strip() or result.stdout.strip())
    return f"https://{project_name}.pages.dev"

# ── Hauptlogik ────────────────────────────────────────────────

def process_card(card, env):
    card_id = card["id"]
    name = card["name"]
    print(f"\n[DEPLOY] {name}")

    tmp_dir = tempfile.mkdtemp()
    try:
        slug = slugify(name)
        links = []

        for suffix in ["1", "2"]:
            demo = f"demo{suffix}"
            print(f"  → R2 Download: {name}/{demo}...")
            ok = download_from_r2(name, demo, tmp_dir)
            if not ok:
                print(f"  ⚠ {demo} nicht in R2 gefunden, übersprungen")
                continue

            demo_folder = Path(tmp_dir) / demo
            project_name = f"{slug}-demo-{suffix}"
            try:
                url = wrangler_deploy(project_name, demo_folder, env)
                links.append(f"Demo {suffix}: {url}")
                print(f"  ✓ {url}")
            except Exception as e:
                print(f"  ✗ Demo {suffix}: {e}")
                links.append(f"Demo {suffix}: ✗ Fehler")

        if links:
            add_comment(card_id, "🚀 Demos deployed:\n\n" + "\n".join(links))
            print(f"  ✓ Links in Trello geschrieben")
        else:
            add_comment(card_id, f"⚠️ Keine Demos in R2 gefunden für: {name}\nBitte zuerst upload_demo.py ausführen.")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def main():
    print("BenderSites Deploy (Railway) — startet...")

    result = subprocess.run(["wrangler", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        print("✗ Wrangler nicht gefunden")
        sys.exit(1)
    print(f"✓ {result.stdout.strip()}")

    env = os.environ.copy()
    env["CLOUDFLARE_ACCOUNT_ID"] = CF_ACCOUNT_ID
    env["CLOUDFLARE_API_TOKEN"]  = CF_API_TOKEN

    try:
        boards = trello_get("/members/me/boards", fields="name,id")
        board = next((b for b in boards if b["name"].lower() == TRELLO_BOARD.lower()), None)
        if not board:
            raise Exception(f"Board '{TRELLO_BOARD}' nicht gefunden. Verfügbar: {[b['name'] for b in boards]}")
        board_id = board["id"]
        print(f"✓ Board: {board['name']}")
    except Exception as e:
        print(f"✗ {e}")
        sys.exit(1)

    print(f"✓ Polling alle {POLL_INTERVAL}s auf '{INTEREST_LIST}'\n")

    while True:
        try:
            lists = trello_get(f"/boards/{board_id}/lists", fields="name,id")
            lst = next((l for l in lists if l["name"].lower() == INTEREST_LIST.lower()), None)
            if not lst:
                raise Exception(f"Liste '{INTEREST_LIST}' nicht gefunden")

            cards = trello_get(f"/lists/{lst['id']}/cards", fields="id,name")
            new_cards = [c for c in cards if c["id"] not in processed]

            if new_cards:
                for card in new_cards:
                    process_card(card, env)
                    processed.add(card["id"])
            else:
                print(f"[{time.strftime('%H:%M:%S')}] Keine neuen Karten — nächste Prüfung in {POLL_INTERVAL}s")

        except Exception as e:
            print(f"✗ Fehler: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
