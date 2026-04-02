# Dev

## Setup

```bash
uv sync
```

## Run

```bash
uv run gitdirector
```

## Tests

```bash
uv run pytest
```

## Format

```bash
uv run black src/ tests/
```

## Lint

```bash
uv run ruff check src/ tests/
```

## Release

1. Bump `version` in `pyproject.toml`
2. Commit and push the version bump
3. Tag and push — GitHub Actions will build and publish to PyPI automatically:

```bash
git tag v<version>
git push origin v<version>
```
