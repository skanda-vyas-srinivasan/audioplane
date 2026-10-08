# Python SDK publication

The first prepared public package is **`audioplane==1.0.0rc1`**, a developer
preview of the Python SDK and CLI. Preparation, a Git source tag, and successful
local tests do not mean that a package has been uploaded to PyPI. Publication is
confirmed only by the PyPI release page and a fresh install from that index.

## First publication verified — 2026-10-07

[`audioplane 1.0.0rc1`](https://pypi.org/project/audioplane/1.0.0rc1/) is
published. Both public files match the validated local artifacts:

| File | SHA-256 |
| --- | --- |
| `audioplane-1.0.0rc1-py3-none-any.whl` | `bf75acff2e6d36a74d4db18e1bcb06bb85b5c0c39acd35978a91edbb18b29629` |
| `audioplane-1.0.0rc1.tar.gz` | `ab606366f7077482b9fa0e6d94335eac64274758b2250641310b43b63c4fe9aa` |

Source checkpoint: `826dc5f04cf4d07622349ab59ed8b80307932515`, tagged
`python-v1.0.0rc1`. The published archives are immutable; later documentation
updates do not modify that source tag or re-upload its artifacts.

Validation before upload: full Runtime release gate including GUI discovery,
200 optional Python tests with no skips, 39 Node tests, strict Twine checks,
archive-content checks, and fresh wheel/source installs. After upload: a new
isolated environment installed the wheel directly from `https://pypi.org/simple`
without cache or `PYTHONPATH`; package/import version, CLI version/help,
installed-package examples and `doctor` against Runtime 1.0.0/protocol v2 passed.

This is SDK publication, not a notarized Runtime or virtual-driver release.
No new authenticated provider or hardware listening test was run for this
upload. The upload token was entered by the human in their own terminal, not
included in chat or committed source.

## What is uploaded

- A pure-Python wheel and source archive built from `SDKs/python`.
- Public `audioplane` APIs, compatible `sonexis` imports, CLI and optional
  provider/MCP integrations. No private recordings, credentials, native build
  products or virtual driver are included.
- The SDK README is the PyPI description. Full guides link to the matching
  `python-v1.0.0rc1` Git source tag, not checkout-relative files.
- The license remains GPL-2.0-or-later, as in the repository.

The native macOS Runtime remains **1.0.0, protocol v2** and is installed
separately. Python SDK prerelease suffixes do not change protocol compatibility.
Version checks enforce the same Runtime base version and consistent Python
package, import, CLI/client and MCP metadata. They do not require the native
Runtime or Node package to become Python prereleases.

## Prepare and check

Use an isolated Python 3.10+ environment for release tools. Run the complete
release gate and optional Python tests before a release checkpoint.

```sh
./Scripts/check-runtime-version.py
./Scripts/test-runtime-release.sh
python -m pip install build twine
python -m build SDKs/python --outdir /absolute/path/to/private-release-directory
python -m twine check --strict /absolute/path/to/private-release-directory/audioplane-1.0.0rc1*
```

Inspect archive contents and install the wheel in a fresh environment without
`PYTHONPATH`. Confirm import and distribution versions, `audioplane version`,
`audioplane agent --help`, and example imports. Install the source archive in
another environment as well. Do not build releases from unrelated dirty files.
Never upload a wildcard pointing at multiple historic release versions.

## First upload: human account setup

1. Create your own account at <https://pypi.org/account/register/>.
2. Verify the email address and enable 2FA at <https://pypi.org/manage/account/>.
   Keep recovery codes in a password manager, not the repository or chat.
3. Create an API token named `AudioPlane first upload`. Before the project
   exists, select **Entire account** scope. After publication, replace it with
   a project-scoped token or configure Trusted Publishing and revoke this token.
4. Upload the two exact validated files with Twine. Supply `__token__` as the
   username; paste the token at its hidden password prompt **in your terminal**.
   Do not send a token to a chat or put it in a shell command/history.

```sh
python -m twine upload --repository-url https://upload.pypi.org/legacy/ \
  --username __token__ \
  /absolute/path/to/audioplane-1.0.0rc1-py3-none-any.whl \
  /absolute/path/to/audioplane-1.0.0rc1.tar.gz
```

PyPI has no browser file-upload operation. An upload requires a token or a
configured Trusted Publisher; a GitHub login alone is not PyPI authorization.
Do not delete/recreate the project or retry with modified files under the same
version if an upload partially succeeds. Check the published files and hashes
first. PyPI does not allow reuse of uploaded filenames.

## After upload

Check <https://pypi.org/project/audioplane/1.0.0rc1/> and verify hashes match
the validated files. In a fresh environment, use:

```sh
python -m pip install --index-url https://pypi.org/simple audioplane==1.0.0rc1
audioplane version
audioplane agent --help
```

Update the public README/install guides only after successful index verification.
Users can select the candidate explicitly or use `--pre`; this is not an
announcement of a notarized Runtime installer or fully qualified production
hardware support. Future uploads use new version numbers.

Official references: [PyPI authentication and account setup](https://pypi.org/help/),
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/), and
[package publishing](https://packaging.python.org/en/latest/tutorials/packaging-projects/).
