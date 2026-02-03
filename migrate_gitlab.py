#!/usr/bin/env python3
import os
import sys
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import quote, urlparse

import requests


CE_URL_ENV = "GITLAB_CE_URL"
EE_URL_ENV = "GITLAB_EE_URL"
CE_TOKEN_ENV = "GITLAB_CE_TOKEN"
EE_TOKEN_ENV = "GITLAB_EE_TOKEN"

INPUT_FILE = "projects.txt"
DEST_ROOT_GROUP = "root-groupB"


def die(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        die(f"Missing environment variable {name}")
    return value.rstrip("/")


def normalize_base_url(url: str) -> str:
    if "://" not in url:
        url = "https://" + url
    return url.rstrip("/")


def api_headers(token: str) -> dict:
    return {"PRIVATE-TOKEN": token}


def api_get(base_url: str, token: str, path: str):
    url = f"{base_url}/api/v4/{path}"
    response = requests.get(url, headers=api_headers(token), timeout=30)
    if response.status_code == 200:
        return response.json()
    if response.status_code == 404:
        return None
    response.raise_for_status()


def api_post(base_url: str, token: str, path: str, payload: dict):
    url = f"{base_url}/api/v4/{path}"
    response = requests.post(url, headers=api_headers(token), json=payload, timeout=30)
    if response.status_code in (200, 201):
        return response.json()
    response.raise_for_status()


def ensure_group(base_url: str, token: str, full_path: str, name: str, parent_id=None):
    encoded = quote(full_path, safe="")
    group = api_get(base_url, token, f"groups/{encoded}")
    if group:
        return group["id"], False

    payload = {"name": name, "path": name}
    if parent_id is not None:
        payload["parent_id"] = parent_id

    try:
        created = api_post(base_url, token, "groups", payload)
        return created["id"], True
    except requests.HTTPError as exc:
        # If created by another run in the meantime, re-fetch.
        response = exc.response
        if response is not None and response.status_code in (400, 409):
            group = api_get(base_url, token, f"groups/{encoded}")
            if group:
                return group["id"], False
        raise


def ensure_project(base_url: str, token: str, full_path: str, name: str, namespace_id: int):
    encoded = quote(full_path, safe="")
    project = api_get(base_url, token, f"projects/{encoded}")
    if project:
        return project["id"], False

    payload = {"name": name, "path": name, "namespace_id": namespace_id}
    try:
        created = api_post(base_url, token, "projects", payload)
        return created["id"], True
    except requests.HTTPError as exc:
        response = exc.response
        if response is not None and response.status_code in (400, 409):
            project = api_get(base_url, token, f"projects/{encoded}")
            if project:
                return project["id"], False
        raise


def git_repo_url(base_url: str, token: str, repo_path: str) -> str:
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    if base_path:
        base_path = base_path + "/"
    return f"{parsed.scheme}://oauth2:{token}@{parsed.netloc}/{base_path}{repo_path}"


def run_git(args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown error"
        raise RuntimeError(f"git failed: {stderr}")


def migrate_repo(ce_url, ce_token, ee_url, ee_token, repo_path_with_git):
    repo_path = repo_path_with_git[:-4] if repo_path_with_git.endswith(".git") else repo_path_with_git
    parts = [p for p in repo_path.split("/") if p]
    if not parts:
        return

    dest_parts = [DEST_ROOT_GROUP] + parts
    project_name = dest_parts[-1]
    group_parts = dest_parts[:-1]

    parent_id = None
    current_path = ""
    for part in group_parts:
        current_path = f"{current_path}/{part}" if current_path else part
        parent_id, created = ensure_group(ee_url, ee_token, current_path, part, parent_id)
        if created:
            print(f"Created group: {current_path}")
        else:
            print(f"Group already exists: {current_path}")

    dest_full_path = "/".join(dest_parts)
    _, created = ensure_project(ee_url, ee_token, dest_full_path, project_name, parent_id)
    if created:
        print(f"Created project: {dest_full_path}")
    else:
        print(f"Project already exists: {dest_full_path}")

    with tempfile.TemporaryDirectory(prefix="gitlab-migrate-") as tmpdir:
        mirror_dir = Path(tmpdir) / "repo.git"
        src_url = git_repo_url(ce_url, ce_token, repo_path_with_git)
        dst_url = git_repo_url(ee_url, ee_token, dest_full_path + ".git")

        print(f"Migrating {repo_path_with_git} -> {dest_full_path}.git")
        run_git(["git", "clone", "--bare", src_url, str(mirror_dir)])
        run_git(["git", "push", "--all", dst_url], cwd=str(mirror_dir))
        run_git(["git", "push", "--tags", dst_url], cwd=str(mirror_dir))


def main():
    ce_url = normalize_base_url(require_env(CE_URL_ENV))
    ee_url = normalize_base_url(require_env(EE_URL_ENV))
    ce_token = require_env(CE_TOKEN_ENV)
    ee_token = require_env(EE_TOKEN_ENV)

    input_path = Path(INPUT_FILE)
    if not input_path.exists():
        die(f"Input file not found: {input_path}")

    lines = input_path.read_text(encoding="utf-8").splitlines()
    repos = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        repos.append(line)

    if not repos:
        die("No repositories found in projects.txt")

    for repo in repos:
        try:
            migrate_repo(ce_url, ce_token, ee_url, ee_token, repo)
        except Exception as exc:
            print(f"Failed to migrate {repo}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
