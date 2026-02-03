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

## Setup

Set the following environment variables:

- `GITLAB_CE_URL` (e.g. `https://gitlab-ce.example.com`)
- `GITLAB_EE_URL` (e.g. `https://gitlab-ee.example.com`)
- `GITLAB_CE_TOKEN`
- `GITLAB_EE_TOKEN`

Create a `projects.txt` file in the same directory. One repo per line:

```
root-group/sub-group/sub-group/project.git
```

## Usage

```
python3 migrate_gitlab.py
```

The script prints whether each group/project already exists or was created,
then pushes all branches and tags to the EE project.
