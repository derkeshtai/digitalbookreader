# Third-party software notices

The original Digital Book Reader code in this repository is licensed under **GNU AGPL v3.0 only** (SPDX: `AGPL-3.0-only`); see [`LICENSE`](LICENSE). Third-party packages remain under their own licenses. This file records the direct requirements in `requirements.txt`; it does not relicense those packages.

## Direct Python dependencies

The license metadata was checked against the listed project releases on 2026-10-16. `requirements.txt` currently uses minimum-version ranges rather than exact pins, so a future installation may resolve to newer releases. Re-check and update this inventory for the exact versions included in any release.

| Package | Requirement | Release checked | License reported by project | Project |
|---|---:|---:|---|---|
| PySide6 | `PySide6>=6.7` | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | [PyPI](https://pypi.org/project/PySide6/) |
| PyMuPDF | `PyMuPDF>=1.24` | 1.28.2 | GNU AGPL v3.0 **or** Artifex Commercial License | [PyPI](https://pypi.org/project/PyMuPDF/) |
| rarfile | `rarfile>=4.2` | 4.5 | ISC | [PyPI](https://pypi.org/project/rarfile/) |
| py7zr | `py7zr>=0.20` | 1.1.4 | LGPL-2.1-or-later | [PyPI](https://pypi.org/project/py7zr/) |
| Markdown | `Markdown>=3.6` | 3.11.1 | BSD-3-Clause | [PyPI](https://pypi.org/project/Markdown/) |
| striprtf | `striprtf>=0.0.33` | 0.0.33 | BSD-3-Clause | [PyPI](https://pypi.org/project/striprtf/) |
| mobi | `mobi>=0.4.1` | 0.4.1 | GPL-3.0-only | [PyPI](https://pypi.org/project/mobi/) |

PyMuPDF is used under its AGPL option for this project; it is not bundled under the Artifex commercial license. The `mobi` package is GPL-3.0-only. PySide6 includes Qt for Python and the Qt WebEngine components; its distribution and the WebEngine/Chromium notices must also be reviewed for the exact wheel being shipped. Distributors must preserve the corresponding notices and comply with the applicable license terms for the complete application and its dependencies.

## External RAR tool

The `rarfile` Python package does not include an archive executable. CBR/RAR support requires a compatible external tool. This repository does **not** contain `UnRAR.exe` or WinRAR. If a distributor chooses to bundle a RAR tool, that executable has separate licensing and redistribution terms that must be reviewed independently.

## Release checklist

Before distributing a Windows installer, resolve and record exact direct and transitive dependency versions, include their required license texts/notices, and review the obligations of AGPL/GPL components for the distribution model. This inventory is informational and is not a legal opinion.
