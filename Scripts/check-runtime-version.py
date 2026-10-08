#!/usr/bin/env python3
"""Check Runtime versions and matching-base Python SDK prerelease metadata."""

import json
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPECTED = (ROOT / "RUNTIME_VERSION").read_text(encoding="utf-8").strip()


def require_version(path: str, pattern: str, expected_count: int = 1,
                    expected: str = EXPECTED) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if len(matches) != expected_count:
        raise AssertionError(
            f"{path}: expected {expected_count} version field(s), found {len(matches)}"
        )
    wrong = [value for value in matches if value != expected]
    if wrong:
        raise AssertionError(f"{path}: expected {expected}, found {wrong}")


def main() -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", EXPECTED):
        raise AssertionError(f"RUNTIME_VERSION is not semantic: {EXPECTED!r}")

    require_version(
        "Sources/SonexisRuntime/IPC/RuntimeProtocol.swift",
        r'runtimeVersion = "([^"]+)"',
    )
    require_version(
        "Distribution/Runtime-Embedded-Info.plist",
        r"<key>CFBundleShortVersionString</key>\s*<string>([^<]+)</string>",
    )

    python_versions = re.findall(r'^version = "([^"]+)"$',
                                (ROOT / "SDKs/python/pyproject.toml").read_text(), re.MULTILINE)
    if len(python_versions) != 1:
        raise AssertionError("Python SDK must declare exactly one package version")
    python_version = python_versions[0]
    if not re.fullmatch(re.escape(EXPECTED) + r"(?:(?:a|b|rc)[0-9]+|\.dev[0-9]+)?", python_version):
        raise AssertionError(f"Python SDK {python_version!r} must match Runtime base {EXPECTED}")
    require_version("SDKs/python/setup.py", r'version="([^"]+)"', expected=python_version)
    require_version("SDKs/python/src/sonexis/client.py", r'client_version: str = "([^"]+)"', expected=python_version)
    require_version("SDKs/python/src/sonexis/mcp_server.py", r'client_version="([^"]+)"', expected=python_version)
    require_version("SDKs/python/src/sonexis/mcp_server.py", r'^\s*version="([^"]+)"', expected=python_version)
    require_version("SDKs/python/src/sonexis/__init__.py", r'__version__ = "([^"]+)"', expected=python_version)
    require_version("SDKs/typescript/src/index.ts", r'client_version: "([^"]+)"', 1)

    classifier = ("Development Status :: 4 - Beta" if python_version != EXPECTED
                  else "Development Status :: 5 - Production/Stable")
    for path in ("SDKs/python/pyproject.toml", "SDKs/python/setup.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        declared = re.findall(r'Development Status :: [^"\n]+', text)
        if declared != [classifier]:
            raise AssertionError(f"{path}: expected classifier {classifier!r}, found {declared}")

    package = json.loads((ROOT / "SDKs/typescript/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "SDKs/typescript/package-lock.json").read_text(encoding="utf-8"))
    values = [package.get("version"), lock.get("version"), lock.get("packages", {}).get("", {}).get("version")]
    if values != [EXPECTED, EXPECTED, EXPECTED]:
        raise AssertionError(f"TypeScript package versions disagree: {values}")

    print(f"Runtime release versions agree: {EXPECTED}; Python SDK: {python_version}")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, ValueError) as error:
        print(f"runtime-version-check: {error}", file=sys.stderr)
        raise SystemExit(1)
