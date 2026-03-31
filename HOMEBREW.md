# Setting Up Homebrew Distribution for GitDirector

This guide explains how to set up and maintain GitDirector as a Homebrew package.

## Option 1: Create a Personal Homebrew Tap (Recommended)

A Homebrew tap is a custom repository of formulas. Follow these steps:

### Step 1: Create a new GitHub repository

Create a repository named `homebrew-gitdirector` in your GitHub account (https://github.com/anitoanto/homebrew-gitdirector)

### Step 2: Clone and set up the tap

```bash
git clone https://github.com/anitoanto/homebrew-gitdirector.git
cd homebrew-gitdirector
mkdir -p Formula
```

### Step 3: Add the formula

Copy the Homebrew formula to your tap:

```bash
cp /path/to/gitdirector/Formula/gitdirector.rb Formula/
git add Formula/gitdirector.rb
git commit -m "Add gitdirector formula"
git push origin main
```

### Step 4: Users can now install via:

```bash
brew tap anitoanto/gitdirector
brew install gitdirector
```

### Step 5: Update the formula

When you release new versions:

1. Update `version` in `Formula/gitdirector.rb`
2. If using a specific release tag, update the `url` to point to the tag
3. Commit and push to the tap repository

```bash
git add Formula/gitdirector.rb
git commit -m "Update gitdirector to vX.Y.Z"
git push origin main
```

Then users can update via:
```bash
brew upgrade gitdirector
```

## Option 2: Submit to Homebrew Core (Official)

For official Homebrew inclusion:

1. Fork https://github.com/Homebrew/homebrew-core
2. Create a formula in `Formula/gitdirector.rb`
3. Submit a pull request with:
   - Working formula that passes `brew audit --strict`
   - Links to releases and documentation
   - No major issues or complaints
4. Maintainers will review and merge

This requires more rigorous testing but makes the formula available via just `brew install gitdirector`

## Current Formula Configuration

The formula (`Formula/gitdirector.rb`):
- **Points to**: Latest commit in `main` branch via `branch: "main"`
- **Entry point**: `gitdirector` command available in user's PATH
- **Dependencies**: Python 3.12 and Git
- **Installation**: Creates a virtualenv and installs package

## Testing the Formula Locally

Before pushing:

```bash
brew install --HEAD ./Formula/gitdirector.rb
gitdirector --help
brew uninstall gitdirector
```

## Important Notes

- The formula currently uses `branch: "main"` to always pull the latest commit
- For production use, consider using tagged releases instead:
  - Update URL: `url "https://github.com/anitoanto/gitdirector/archive/refs/tags/v#{version}.tar.gz"`
  - This ensures stability across different installations
- Rich is included in dependencies and will be installed automatically
- The virtualenv approach ensures isolated dependencies
