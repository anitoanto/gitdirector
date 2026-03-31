# GitDirector - Feature Brainstorm

## Core Sync Operations
- Pull all repos
- Sync all repos (pull + push)
- Fetch all remotes without pulling
- Push changes across all repos

## Status & Monitoring
- See which repos have uncommitted changes
- Show repos that are ahead/behind remote
- Display current branch for each repo
- Alert on repos with conflicts or issues
- Show last commit info per repo

## Selective Operations
- Filter repos by name/path pattern
- Run commands only on "dirty" repos (with changes)
- Run commands only on specific branches
- Skip certain repos
- Run operations only on repos that are behind

## Branch Management
- Check branches across all repos
- Switch all repos to a specific branch
- Create/delete branches in all repos
- Show branch status

## Safety & Maintenance
- Dry-run mode to preview actions
- Git clean operations (remove untracked files)
- Stash changes before pulling
- Validate all repos are valid git repos
- Backup/restore config

## Configuration
- Group repos into profiles/workspaces
- Per-repo settings (skip certain repos, auto-stash, etc.)
- Exclude patterns

## Reporting
- Generate summary reports
- Export operation logs
- Show which repos failed
