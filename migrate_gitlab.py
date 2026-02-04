#!/usr/bin/env python3
import base64
import os
import re
import sys
import time
import tempfile
from pathlib import Path
from urllib.parse import quote

import requests


CE_URL_ENV = "GITLAB_CE_URL"
EE_URL_ENV = "GITLAB_EE_URL"
CE_TOKEN_ENV = "GITLAB_CE_TOKEN"
EE_TOKEN_ENV = "GITLAB_EE_TOKEN"
INPUT_FILE_ENV = "GITLAB_PROJECTS_FILE"
DEST_ROOT_ENV = "GITLAB_DEST_ROOT_GROUP"
INCLUDE_PREFIX_ENV = "GITLAB_INCLUDE_PREFIX"

EXPORT_POLL_SECONDS = 5
IMPORT_POLL_SECONDS = 5
MAX_POLL_MINUTES = 60


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


def api_get(base_url: str, token: str, path: str, params=None):
    url = f"{base_url}/api/v4/{path}"
    response = requests.get(url, headers=api_headers(token), params=params, timeout=60)
    if response.status_code == 200:
        return response.json()
    if response.status_code == 404:
        return None
    response.raise_for_status()


def api_get_all(base_url: str, token: str, path: str, params=None):
    items = []
    page = 1
    while True:
        req_params = {"per_page": 100, "page": page}
        if params:
            req_params.update(params)
        payload = api_get(base_url, token, path, params=req_params)
        if not payload:
            break
        if isinstance(payload, dict):
            items.append(payload)
            break
        items.extend(payload)
        page += 1
    return items


def api_post(base_url: str, token: str, path: str, payload=None):
    url = f"{base_url}/api/v4/{path}"
    response = requests.post(url, headers=api_headers(token), json=payload, timeout=60)
    if response.status_code in (200, 201, 202):
        if response.content:
            return response.json()
        return None
    response.raise_for_status()


def api_post_form(base_url: str, token: str, path: str, data=None, files=None):
    url = f"{base_url}/api/v4/{path}"
    response = requests.post(url, headers=api_headers(token), data=data, files=files, timeout=300)
    if response.status_code in (200, 201, 202):
        return response.json()
    response.raise_for_status()


def api_put(base_url: str, token: str, path: str, payload: dict):
    url = f"{base_url}/api/v4/{path}"
    response = requests.put(url, headers=api_headers(token), json=payload, timeout=60)
    if response.status_code in (200, 201):
        return response.json()
    response.raise_for_status()


def api_put_form(base_url: str, token: str, path: str, data=None):
    url = f"{base_url}/api/v4/{path}"
    response = requests.put(url, headers=api_headers(token), data=data, timeout=60)
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
        response = exc.response
        if response is not None and response.status_code in (400, 409):
            group = api_get(base_url, token, f"groups/{encoded}")
            if group:
                return group["id"], False
        raise


def get_project(base_url: str, token: str, project_path_or_id: str):
    encoded = quote(str(project_path_or_id), safe="")
    return api_get(base_url, token, f"projects/{encoded}")


def wait_for_export(base_url: str, token: str, project_id: int):
    deadline = time.time() + MAX_POLL_MINUTES * 60
    while time.time() < deadline:
        status = api_get(base_url, token, f"projects/{project_id}/export")
        if not status:
            time.sleep(EXPORT_POLL_SECONDS)
            continue
        export_status = status.get("export_status") or status.get("status")
        if export_status == "finished":
            return
        if export_status in ("failed",):
            raise RuntimeError(f"Export failed for project {project_id}: {status}")
        time.sleep(EXPORT_POLL_SECONDS)
    raise RuntimeError(f"Export timed out for project {project_id}")


def download_export(base_url: str, token: str, project_id: int, dest_path: Path):
    url = f"{base_url}/api/v4/projects/{project_id}/export/download"
    with requests.get(url, headers=api_headers(token), stream=True, timeout=300) as response:
        response.raise_for_status()
        with dest_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def wait_for_import(base_url: str, token: str, project_id: int):
    deadline = time.time() + MAX_POLL_MINUTES * 60
    while time.time() < deadline:
        status = api_get(base_url, token, f"projects/{project_id}/import")
        if not status:
            time.sleep(IMPORT_POLL_SECONDS)
            continue
        import_status = status.get("import_status") or status.get("status")
        if import_status == "finished":
            return
        if import_status == "failed":
            raise RuntimeError(f"Import failed for project {project_id}: {status.get('import_error')}")
        time.sleep(IMPORT_POLL_SECONDS)
    raise RuntimeError(f"Import timed out for project {project_id}")


def build_issue_maps(ee_issues):
    by_iid = {issue.get("iid"): issue for issue in ee_issues if issue.get("iid")}
    by_title_created = {}
    for issue in ee_issues:
        key = (issue.get("title"), issue.get("created_at"))
        if key[0] and key[1]:
            by_title_created[key] = issue
    return by_iid, by_title_created


def get_file(base_url: str, token: str, project_id: int, file_path: str, ref: str):
    encoded_path = quote(file_path, safe="")
    return api_get(
        base_url,
        token,
        f"projects/{project_id}/repository/files/{encoded_path}",
        params={"ref": ref},
    )


def update_file(base_url: str, token: str, project_id: int, file_path: str, branch: str, content: str):
    encoded_path = quote(file_path, safe="")
    payload = {
        "branch": branch,
        "content": content,
        "commit_message": f"Update {file_path} include paths",
    }
    return api_put_form(base_url, token, f"projects/{project_id}/repository/files/{encoded_path}", payload)


def prefix_infra_includes(ci_text: str, prefix: str) -> str:
    if "include" not in ci_text or "infra/" not in ci_text:
        return ci_text

    def replace_infra(line: str) -> str:
        if "infra/" not in line:
            return line
        return re.sub(r"(?<!viridien/)infra/", f"{prefix}infra/", line)

    lines = ci_text.splitlines()
    out = []
    in_include = False
    include_indent = None

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if stripped.startswith("include:"):
            in_include = True
            include_indent = indent
            line = replace_infra(line)
        else:
            if in_include:
                if stripped and indent <= include_indent:
                    in_include = False
                else:
                    line = replace_infra(line)
        out.append(line)

    result = "\n".join(out)
    if ci_text.endswith("\n"):
        result += "\n"
    return result


def update_ci_includes(ee_url, ee_token, ee_project, include_prefix: str):
    project_id = ee_project["id"]
    default_branch = ee_project.get("default_branch") or "main"
    file_info = get_file(ee_url, ee_token, project_id, ".gitlab-ci.yml", default_branch)
    if not file_info:
        return

    content_b64 = file_info.get("content") or ""
    try:
        ci_text = base64.b64decode(content_b64).decode("utf-8")
    except Exception:
        raise RuntimeError("Failed to decode .gitlab-ci.yml content")

    updated = prefix_infra_includes(ci_text, prefix=include_prefix)
    if updated == ci_text:
        print("No .gitlab-ci.yml include updates needed.")
        return

    update_file(ee_url, ee_token, project_id, ".gitlab-ci.yml", default_branch, updated)
    print("Updated .gitlab-ci.yml include paths.")


def ce_has_migration_note(ce_notes, ee_issue_url: str) -> bool:
    if not ee_issue_url:
        return False
    needle = f"Migrated to EE: {ee_issue_url}"
    for note in ce_notes:
        if note.get("body", "").strip() == needle:
            return True
    return False


def close_ce_issue_with_link(ce_url, ce_token, ce_project_id, ce_iid, ee_issue_url):
    ce_notes = api_get_all(
        ce_url,
        ce_token,
        f"projects/{ce_project_id}/issues/{ce_iid}/notes",
        params={"order_by": "created_at", "sort": "asc"},
    )
    if ee_issue_url and not ce_has_migration_note(ce_notes, ee_issue_url):
        api_post(
            ce_url,
            ce_token,
            f"projects/{ce_project_id}/issues/{ce_iid}/notes",
            {"body": f"Migrated to EE: {ee_issue_url}"},
        )
    api_put(
        ce_url,
        ce_token,
        f"projects/{ce_project_id}/issues/{ce_iid}",
        {"state_event": "close"},
    )


def reconcile_issues(ce_url, ce_token, ee_url, ee_token, ce_project, ee_project):
    ce_project_id = ce_project["id"]
    ee_project_id = ee_project["id"]

    ce_issues = api_get_all(
        ce_url,
        ce_token,
        f"projects/{ce_project_id}/issues",
        params={"state": "all", "order_by": "iid", "sort": "asc"},
    )
    ee_issues = api_get_all(
        ee_url,
        ee_token,
        f"projects/{ee_project_id}/issues",
        params={"state": "all", "order_by": "iid", "sort": "asc"},
    )

    if len(ce_issues) != len(ee_issues):
        print(
            f"Warning: issue count mismatch CE={len(ce_issues)} EE={len(ee_issues)} for {ee_project.get('path_with_namespace')}"
        )

    ee_by_iid, ee_by_title_created = build_issue_maps(ee_issues)

    for ce_issue in ce_issues:
        ee_issue = ee_by_iid.get(ce_issue.get("iid"))
        if not ee_issue:
            key = (ce_issue.get("title"), ce_issue.get("created_at"))
            ee_issue = ee_by_title_created.get(key)
        if not ee_issue:
            print(f"Warning: Could not find EE issue for CE IID {ce_issue.get('iid')}")
            continue

        if ce_issue.get("state") == "opened":
            ee_url_link = ee_issue.get("web_url")
            if ee_url_link:
                close_ce_issue_with_link(ce_url, ce_token, ce_project_id, ce_issue["iid"], ee_url_link)
                print(f"Closed CE issue {ce_issue['iid']} with link to EE issue.")


def export_project(ce_url, ce_token, ce_project_id: int):
    api_post(ce_url, ce_token, f"projects/{ce_project_id}/export")
    wait_for_export(ce_url, ce_token, ce_project_id)


def import_project(ee_url, ee_token, export_file: Path, dest_namespace: str, dest_path: str, dest_name: str):
    with export_file.open("rb") as handle:
        files = {"file": handle}
        data = {
            "path": dest_path,
            "name": dest_name,
            "namespace_path": dest_namespace,
        }
        return api_post_form(ee_url, ee_token, "projects/import", data=data, files=files)


def migrate_repo(ce_url, ce_token, ee_url, ee_token, repo_path_with_git, dest_root_group, include_prefix):
    repo_path = repo_path_with_git[:-4] if repo_path_with_git.endswith(".git") else repo_path_with_git
    parts = [p for p in repo_path.split("/") if p]
    if not parts:
        return

    dest_parts = [dest_root_group] + parts
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

    ce_project = get_project(ce_url, ce_token, repo_path)
    if not ce_project:
        raise RuntimeError(f"CE project not found: {repo_path}")

    ee_project = get_project(ee_url, ee_token, dest_full_path)
    if ee_project:
        print(f"Project already exists in EE: {dest_full_path}")
        update_ci_includes(ee_url, ee_token, ee_project, include_prefix)
        reconcile_issues(ce_url, ce_token, ee_url, ee_token, ce_project, ee_project)
        return

    print(f"Exporting project from CE: {repo_path}")
    export_project(ce_url, ce_token, ce_project["id"])

    with tempfile.TemporaryDirectory(prefix="gitlab-export-") as tmpdir:
        export_file = Path(tmpdir) / f"{project_name}-export.tar.gz"
        download_export(ce_url, ce_token, ce_project["id"], export_file)

        print(f"Importing project to EE: {dest_full_path}")
        imported = import_project(
            ee_url,
            ee_token,
            export_file,
            dest_namespace="/".join(group_parts),
            dest_path=project_name,
            dest_name=project_name,
        )

    ee_project_id = imported.get("id") if imported else None
    if not ee_project_id:
        ee_project = get_project(ee_url, ee_token, dest_full_path)
        if not ee_project:
            raise RuntimeError(f"Import did not return project info for {dest_full_path}")
        ee_project_id = ee_project["id"]
    else:
        ee_project = get_project(ee_url, ee_token, ee_project_id)

    wait_for_import(ee_url, ee_token, ee_project_id)
    ee_project = get_project(ee_url, ee_token, ee_project_id)
    update_ci_includes(ee_url, ee_token, ee_project, include_prefix)
    reconcile_issues(ce_url, ce_token, ee_url, ee_token, ce_project, ee_project)


def main():
    ce_url = normalize_base_url(require_env(CE_URL_ENV))
    ee_url = normalize_base_url(require_env(EE_URL_ENV))
    ce_token = require_env(CE_TOKEN_ENV)
    ee_token = require_env(EE_TOKEN_ENV)

    input_file = os.getenv(INPUT_FILE_ENV)
    if not input_file:
        die(f"Missing environment variable {INPUT_FILE_ENV}")

    dest_root_group = os.getenv(DEST_ROOT_ENV)
    if not dest_root_group:
        die(f"Missing environment variable {DEST_ROOT_ENV}")
    dest_root_group = dest_root_group.strip().strip("/")
    if not dest_root_group:
        die(f"{DEST_ROOT_ENV} cannot be empty after stripping slashes")

    include_prefix = os.getenv(INCLUDE_PREFIX_ENV, "").strip()
    if include_prefix and not include_prefix.endswith("/"):
        include_prefix = include_prefix + "/"
    if not include_prefix:
        include_prefix = "viridien/"

    input_path = Path(input_file)
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
        die(f"No repositories found in {input_file}")

    for repo in repos:
        try:
            migrate_repo(ce_url, ce_token, ee_url, ee_token, repo, dest_root_group, include_prefix)
        except Exception as exc:
            print(f"Failed to migrate {repo}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
