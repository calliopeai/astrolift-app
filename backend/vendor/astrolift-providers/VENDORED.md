## Vendored from astrolift-providers

This directory is a snapshot of the `astrolift-providers` source tree,
copied in so the backend container image can ship with provider plugins
loaded out-of-the-box. The upstream repo is private, so a git submodule
won't clone in upstream CI without extra credentials — vendoring keeps
the build self-contained.

To refresh:

```bash
rsync -av --delete \
  --exclude='.git' --exclude='tests' \
  --exclude='__pycache__' --exclude='.ruff_cache' --exclude='.mypy_cache' \
  /path/to/astrolift-providers/ backend/vendor/astrolift-providers/
```

Then update `SOURCE_COMMIT` below and commit.

- **SOURCE_COMMIT**: `7b6d1da5b26b2dbd540044f94a3763f8f39be4be`
- **Upstream repo**: `git@github.com:calliopeai/astrolift-providers.git`
