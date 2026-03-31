# GitDirector

A Python CLI tool for managing and synchronizing multiple git repositories with ease.

## Overview

GitDirector simplifies the management of multiple git repositories by providing a unified interface to perform common git operations across all your repos simultaneously. Never again wonder if your local repositories are in sync with their remotes.

## Features

- **Status Overview**: Quick overview of which repos are up-to-date or need attention
- **Batch Pull**: Pull latest changes from remotes across all repositories
- **Add Repositories**: Add specific repos or discover all repos in a folder recursively
- **List Repositories**: View all tracked repositories
- **Remove Repositories**: Stop tracking specific repositories

## Installation

Install GitDirector via pip:

```bash
pip install gitdirector
```

Or install from source:

```bash
git clone <repository-url>
cd gitdirector
pip install -e .
```

## Usage

### Add Repositories

Add repositories to tracking in two ways:

**Add a specific repository:**

```bash
gitdirector add /path/to/repo
```

**Discover and add all repositories in a folder (recursively):**

```bash
gitdirector add /path/to/folder --discover
```

This will recursively search through all subdirectories and automatically add all discovered git repositories.

### Check Status Across All Repos

View the status of all tracked repositories:

```bash
gitdirector status
```

### Pull All Repositories

Fetch and pull the latest changes from all remotes:

```bash
gitdirector pull
```

### List Tracked Repositories

Display all repositories currently being tracked:

```bash
gitdirector list
```

### Remove Repositories

Stop tracking repositories in two ways:

**Remove a specific repository:**

```bash
gitdirector remove /path/to/repo
```

**Remove all repositories in a folder (recursively):**

```bash
gitdirector remove /path/to/folder --discover
```

This will recursively search through all subdirectories and automatically remove all tracked git repositories found within.

## Configuration

GitDirector stores repository information in `~/.gitdirector/config.yaml`. The tool automatically manages this configuration file when you add or remove repositories.

Example configuration structure:

```yaml
repositories:
  - path: /path/to/repo1
  - path: /path/to/repo2
```

## Requirements

- Python 3.7+
- Git (must be installed and accessible from command line)

## Contributing

Contributions are welcome! Feel free to submit issues and pull requests.

## License

MIT License - See LICENSE file for details
