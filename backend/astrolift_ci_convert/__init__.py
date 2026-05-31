"""
astrolift-ci-convert — convert GitHub Actions and GitLab CI pipelines to Astrolift TOML.

Standalone package with zero Django dependencies.

Usage (library)::

    from astrolift_ci_convert.gha import parse, convert
    toml_str = convert(parse(yaml_str))

    from astrolift_ci_convert.gitlab import parse, convert
    toml_str = convert(parse(yaml_str))

CLI::

    alci convert github .github/workflows/ci.yml
    alci convert gitlab .gitlab-ci.yml
"""

__version__ = "0.1.0"
