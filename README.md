# GitLab CE to EE Migration (group remap)

This project migrates GitLab repositories listed in `projects.txt` from a GitLab
Community Edition (CE) instance to a GitLab Enterprise Edition (EE) instance.
It also remaps the namespace by inserting a new root group.

Example:
`root-group/sub-group/sub-group/project.git` becomes
`root-groupB/root-group/sub-group/sub-group/project.git` in EE.

## How it works

- Reads `projects.txt` from the current folder.
- Creates missing groups and sub-groups in EE.
- Creates the project in EE if needed.
- Clones the CE repo as a bare repo and pushes branches and tags to EE.

## Requirements

- Python 3
- `git`
- Python package: `requests`

## Setup Instructions

### 1. Install Dependencies

```bash
pip install requests
```

Make sure `git` is installed and available in your PATH.

### 2. Create Access Tokens

Create personal access tokens for both GitLab instances:

- **Source GitLab (CE)**: Token needs `read_api` and `read_repository` scopes
- **Destination GitLab (EE)**: Token needs `api` scope (to create groups/projects and push code)

### 3. Create Projects List File

Create a text file (e.g., `projects.txt`) with one repository path per line:

```
root-group/sub-group/project.git
another-group/project2.git
# Comments are supported
team/backend/api.git
```

### 4. Set Environment Variables

Set all **6 required** environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `GITLAB_CE_URL` | Source GitLab instance URL | `https://gitlab-ce.example.com` |
| `GITLAB_EE_URL` | Destination GitLab instance URL | `https://gitlab-ee.example.com` |
| `GITLAB_CE_TOKEN` | Source GitLab access token | `glpat-xxxxxxxxxxxx` |
| `GITLAB_EE_TOKEN` | Destination GitLab access token | `glpat-yyyyyyyyyyyy` |
| `GITLAB_PROJECTS_FILE` | Path to projects list file | `./projects.txt` |
| `GITLAB_DEST_ROOT_GROUP` | Root group name in destination | `migrated-repos` |

**Example:**

```bash
export GITLAB_CE_URL="https://gitlab-ce.example.com"
export GITLAB_EE_URL="https://gitlab-ee.example.com"
export GITLAB_CE_TOKEN="glpat-xxxxxxxxxxxx"
export GITLAB_EE_TOKEN="glpat-yyyyyyyyyyyy"
export GITLAB_PROJECTS_FILE="./projects.txt"
export GITLAB_DEST_ROOT_GROUP="migrated-repos"
```

## Usage

### Run the Migration

```bash
python3 migrate_gitlab.py
```

### One-Liner Example

```bash
GITLAB_CE_URL="https://gitlab-ce.example.com" \
GITLAB_EE_URL="https://gitlab-ee.example.com" \
GITLAB_CE_TOKEN="glpat-xxxxxxxxxxxx" \
GITLAB_EE_TOKEN="glpat-yyyyyyyyyyyy" \
GITLAB_PROJECTS_FILE="./projects.txt" \
GITLAB_DEST_ROOT_GROUP="migrated-repos" \
python3 migrate_gitlab.py
```

## What the Script Does

The script will:
1. Read the list of repositories from your projects file
2. For each repository:
   - Create the destination group hierarchy in EE (if it doesn't exist)
   - Create the project in EE (if it doesn't exist)
   - Clone the repository from CE as a bare mirror
   - Push all branches and tags to EE

The script prints whether each group/project already exists or was created,
then pushes all branches and tags to the EE project.
