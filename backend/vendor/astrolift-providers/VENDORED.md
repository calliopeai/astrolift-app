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

- **SOURCE_COMMIT**: `9f0e346` (partial — `_sdk/cost.py`, `aws/cost.py`, `gcp/cost.py`, `azure/cost.py` synced for #502 billing-actuals on top of #490 notification sync at `38c2a29`)
- **Upstream repo**: `git@github.com:calliopeai/astrolift-providers.git`
