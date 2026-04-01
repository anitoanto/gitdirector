# GitDirector Development Guide

This guide covers development setup, testing, building, and publishing GitDirector.

## Development Setup

### Prerequisites

- Python 3.9 or higher
- [uv](https://docs.astral.sh/uv/) - Fast Python package installer and resolver

### Initial Setup

1. Clone the repository:
```bash
git clone https://github.com/anitoanto/gitdirector.git
cd gitdirector
```

2. Install uv (if not already installed):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. Create a virtual environment and install dependencies:
```bash
uv sync
```

This will install all dependencies and dev dependencies in an isolated environment.

### Sync Dependencies

After pulling changes that modify dependencies:
```bash
uv sync
```

## Project Structure

```
gitdirector/
├── src/gitdirector/          # Main package source code
│   ├── __init__.py
│   ├── cli.py                # CLI entry point
│   ├── core.py               # Core functionality
│   ├── config.py             # Configuration management
│   └── utils.py              # Utility functions
├── tests/                    # Test suite
├── pyproject.toml            # Project configuration (includes build system)
├── README.md                 # User documentation
├── DEV.md                    # This file (development documentation)
└── LICENSE                   # MIT License
```

## Code Quality & Formatting

### Format Code

Use Black for consistent code formatting:
```bash
uv run black src/ tests/
```

### Lint Code

Run Ruff for linting:
```bash
uv run ruff check src/ tests/
```

Auto-fix linting issues:
```bash
uv run ruff check --fix src/ tests/
```

### Type Checking

Run MyPy for type checking:
```bash
uv run mypy src/
```

### All Code Quality Checks

Run all checks in sequence:
```bash
uv run black src/ tests/ && uv run ruff check src/ tests/ && uv run mypy src/
```

## Testing

### Run Tests

Run the entire test suite:
```bash
uv run pytest
```

Run with coverage report:
```bash
uv run pytest --cov=src/gitdirector --cov-report=html
```

This generates an HTML coverage report in `htmlcov/index.html`.

Run specific test file:
```bash
uv run pytest tests/test_cli.py
```

Run with verbose output:
```bash
uv run pytest -v
```

## Building

### Build Wheel and Source Distribution

Build both wheel (`.whl`) and source distribution (`.tar.gz`):
```bash
uv build
```

This creates:
 - `dist/gitdirector-0.1.1-py3-none-any.whl` (wheel)
 - `dist/gitdirector-0.1.1.tar.gz` (source distribution)

### Build Only Wheel

```bash
uv build --wheel
```

### Build Only Source Distribution

```bash
uv build --sdist
```

### Output Location

All built artifacts are placed in the `dist/` directory.

## Publishing to PyPI

### Prerequisites for Publishing

1. Create a PyPI account at https://pypi.org/account/register/
2. Create API token at https://pypi.org/manage/account/
3. Configure local credentials (choose one):

**Option A: Using .pypirc file**
```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
repository: https://upload.pypi.org/legacy/
username: __token__
password: pypi_YOUR_ACTUAL_TOKEN_HERE

[testpypi]
repository: https://test.pypi.org/legacy/
username: __token__
password: pypi_YOUR_TEST_TOKEN_HERE
```

Save to `~/.pypirc` with permissions: `chmod 600 ~/.pypirc`

**Option B: Using environment variables (recommended for CI/CD)**
```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi_YOUR_ACTUAL_TOKEN_HERE
```

### Publish to TestPyPI (Recommended First Step)

Test your package before publishing to production:
```bash
uv run twine upload --repository testpypi dist/*
```

Then test installation:
```bash
pip install --index-url https://test.pypi.org/simple/ gitdirector
```

### Publish to PyPI (Production)

```bash
uv run twine upload dist/*
```

Or specify credentials:
```bash
uv run twine upload --username __token__ --password $(echo $PYPI_TOKEN) dist/*
```

### Full Publishing Workflow

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md` (if you have one) with changes
3. Run all checks and tests:
   ```bash
   uv run black src/ tests/
   uv run ruff check --fix src/ tests/
   uv run mypy src/
   uv run pytest
   ```
4. Clean previous builds:
   ```bash
   rm -rf dist/ build/ *.egg-info
   ```
5. Build new artifacts:
   ```bash
   uv build
   ```
6. Test on TestPyPI:
   ```bash
   uv run twine upload --repository testpypi dist/*
   ```
7. Verify on TestPyPI, then publish to PyPI:
   ```bash
   uv run twine upload dist/*
   ```

## Weird/Important Commands & Notes

### 1. **Clean Build Artifacts**

Before rebuilding, clean old artifacts:
```bash
rm -rf dist/ build/ src/*.egg-info
```

**Why:** Sometimes old build artifacts can interfere with the new build.

### 2. **Regenerate Lock File**

If you need to regenerate the lock file (normally handled by `uv`):
```bash
uv lock --upgrade
```

### 3. **Install Package in Editable Mode**

For development (allows live code changes):
```bash
uv pip install -e .
```

### 4. **Check Package Contents Before Publishing**

Verify what will be included in the wheel:
```bash
unzip -l dist/gitdirector-0.1.1-py3-none-any.whl
```

### 5. **Dry Run Upload**

Test upload without actually uploading:
```bash
uv run twine check dist/*
```

This validates your packages are correct before uploading.

### 6. **View Package Metadata**

Check what PyPI will see:
```bash
uv run twine check dist/* --verbose
```

### 7. **Environment Variables for CI/CD**

When using CI/CD (GitHub Actions, etc.), use repository secrets:
```yaml
env:
  TWINE_USERNAME: __token__
  TWINE_PASSWORD: ${{ secrets.PYPI_TOKEN }}
```

### 8. **Troubleshooting Wheel vs SDist**

Some users may need the source distribution for compilation:
- **Wheel (.whl)**: Pre-built, faster installation, no compilation
- **SDist (.tar.gz)**: Source code, user compiles, slower but more compatible

Always provide both.

### 9. **Version Bumping**

Update version in `pyproject.toml` before release. Use semantic versioning:
```
MAJOR.MINOR.PATCH
- MAJOR: Breaking changes
- MINOR: New features, backward compatible
- PATCH: Bug fixes
```

### 10. **Check for Unreleased Files**

Before publishing, ensure no sensitive files are included:
```bash
tar -tzf dist/gitdirector-0.1.1.tar.gz | grep -E "(\.env|credentials|secret)"
```

## Dependency Management

### Add New Dependency

```bash
uv add package_name
```

Add as dev dependency:
```bash
uv add --dev package_name
```

### Remove Dependency

```bash
uv remove package_name
```

### Update Specific Dependency

```bash
uv pip install --upgrade package_name
```

### Pin Specific Version

Edit `pyproject.toml` directly or use:
```bash
uv add 'package_name==1.2.3'
```

## Useful Environment Variables

```bash
# For building with uv_build
export UV_BUILD_LOG_LEVEL=debug

# For development
export GITDIRECTOR_DEBUG=1
```

## Running the CLI Locally

After setup, run the CLI directly:
```bash
uv run gitdirector --help
```

Or if you synced with `uv sync`:
```bash
gitdirector --help
```

## Documentation

- **README.md**: User-facing documentation
- **DEV.md**: This file (development documentation)
- **Docstrings**: Add comprehensive docstrings to functions and classes

## Troubleshooting

### Issue: `uv: command not found`

Install uv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Issue: Module not found errors

Ensure you've synced dependencies:
```bash
uv sync
```

### Issue: Import errors in tests

Make sure the package is properly installed:
```bash
uv pip install -e .
```

### Issue: Black or Ruff not found

These should be installed via `uv sync`. If not:
```bash
uv add --dev black ruff pytest
```

## Release Checklist

- [ ] Update version in `pyproject.toml`
- [ ] Update README.md with new features/changes
- [ ] Run all tests: `uv run pytest`
- [ ] Run code quality checks: `black`, `ruff`, `mypy`
- [ ] Clean build artifacts: `rm -rf dist/ build/`
- [ ] Build distribution: `uv build`
- [ ] Test on TestPyPI: `uv run twine upload --repository testpypi dist/*`
- [ ] Verify TestPyPI installation works
 - [ ] Create git tag: `git tag v0.1.1`
- [ ] Publish to PyPI: `uv run twine upload dist/*`
 - [ ] Push tag: `git push origin v0.1.1`
- [ ] Create GitHub release

---

For more information on uv, see the [uv documentation](https://docs.astral.sh/uv/).
