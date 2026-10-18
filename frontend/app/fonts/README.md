# Locally built fonts

These files replace `next/font/google` build-time requests. The font families,
normal styles, requested weights, CSS variables, `swap` display behavior, and
CSS fallback stacks remain the same. IBM Plex Sans is a variable face covering
the configured 300–700 range; IBM Plex Mono uses the configured static weights
400, 500, and 600. DM Sans remains variable (weight axis 100–1000), matching
the prior variable-font usage.

Assets and the per-family SIL Open Font License files are copied from the
official Google Fonts repository at immutable commit
[`47d54ca1ebb14fd864695f1d39dc734f39017c33`](https://github.com/google/fonts/tree/47d54ca1ebb14fd864695f1d39dc734f39017c33).
The CI log did not retain a Google Fonts response body or URL, so this change
does not claim to identify the exact response that failed.

| Family | Upstream path | Local file | SHA-256 |
|---|---|---|---|
| DM Sans | `ofl/dmsans/DMSans[opsz,wght].ttf` | `dm-sans/DMSans-VF.ttf` | `8cd08d97e89c24d0aa92edd2f0f4c8ee6195eee9b7c9f154865a58b02f0c1c0d` |
| IBM Plex Sans | `ofl/ibmplexsans/IBMPlexSans[wdth,wght].ttf` | `ibm-plex-sans/IBMPlexSans-VF.ttf` | `3b031aa4216174205bd8471f88a9b91f093169e9e87bd5262242bc5967fe2e3` |
| IBM Plex Mono | `ofl/ibmplexmono/IBMPlexMono-Regular.ttf` | `ibm-plex-mono/IBMPlexMono-Regular.ttf` | `6a3412f058c7d8dfd9170c41e85ade48e5156ecb89356110ca57a0a27734af46` |
| IBM Plex Mono | `ofl/ibmplexmono/IBMPlexMono-Medium.ttf` | `ibm-plex-mono/IBMPlexMono-Medium.ttf` | `a9b4c49bb299e05b5f6c481e7fb5e78943d2793249a0c8874ab574a2d1ea6755` |
| IBM Plex Mono | `ofl/ibmplexmono/IBMPlexMono-SemiBold.ttf` | `ibm-plex-mono/IBMPlexMono-SemiBold.ttf` | `d3c38e55c78f5b0f28009fddba4834ec503278936a5986032424c9bd2d23aa46` |

The three upstream `OFL.txt` files are retained beside their family assets.
The complete asset directory is about 1.2 MiB. This removes Google Fonts
network access from the production build; it does not promise pixel-identical
rendering across browsers or operating systems.

All three licenses are SIL Open Font License 1.1. Their local SHA-256 values
are `dm-sans/OFL.txt` `9af36190332437f5ecd09974de43c1f7c77a310a996cdd8ceb25628b458840e1`,
`ibm-plex-sans/OFL.txt` and `ibm-plex-mono/OFL.txt` both
`7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da`.
