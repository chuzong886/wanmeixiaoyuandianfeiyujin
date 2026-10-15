"""Scan the exact Git index; emit file/rule only, never matching credentials."""
import re
import subprocess
import sys

RULES = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "provider-access-token": rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|LTAI[A-Za-z0-9]{16,}|AKIA[A-Z0-9]{16})",
    "personal-deployment": rb"(?:[A-Za-z0-9-]+\.)*chuzong\.top|[A-Za-z0-9._%+-]+@163\.com|C:[/\\]Users[/\\](?!Public\b|Default\b)[^/\\\s]+",
    "captcha-prefix": rb"esa-(?!example\b|your\b)[a-z0-9]{8,}",
    "spug-live-url": rb"https?://push\.spug\.cc/(?:send|sms)/(?!test\b|DEMO_TOKEN\b|YOUR_TOKEN\b)[A-Za-z0-9_-]{4,}",
    "identity-literal": rb'''(?:account|school_code|college_code|customercode|roomverify|phone)["']?\s*(?:=>|:|=)\s*["'][0-9]{5,}["']''',
    "literal-key": rb'''(?:edge_secret|app_key|api_key|access_token)["']?\s*(?:=>|:|=)\s*["'][A-Za-z0-9_+/=-]{24,}["']''',
    "redaction-marker": rb"REDACTED[_]PRIVATE[_]VALUE",
    "static-query-example": rb'''(?:account|school_code|college_code|customercode|roomverify)["']?\s*(?:=>|:|=)\s*["'][A-Za-z0-9_-]{4,}["']''',
}
BANNED_SUFFIXES = (".zip", ".7z", ".tar", ".gz", ".pem", ".key", ".p12", ".pfx", ".log", ".har", ".cookie", ".sqlite", ".db")

def git(*args):
    return subprocess.check_output(["git", *args])

def main():
    files = git("ls-files", "--stage", "-z").split(b"\0")
    failed = []
    count = 0
    for row in files:
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        path = raw_path.decode("utf-8")
        count += 1
        if mode not in (b"100644", b"100755") or stage != b"0":
            failed.append((path, "unsupported-index-entry"))
            continue
        if ((path.startswith("storage/") and path != "storage/.gitkeep")
                or path.startswith(("private/", "dist/", "outputs/", "backups/", ".test-runtime/", ".runtimes/"))
                or path.endswith(BANNED_SUFFIXES) or (path.endswith(".sql") and path != "schema.sql")
                or path.rsplit("/", 1)[-1] in ("config.php", "auth-setup.json", ".env")):
            failed.append((path, "runtime-or-private-file"))
        blob = git("cat-file", "blob", oid.decode("ascii"))
        for name, pattern in RULES.items():
            if re.search(pattern, blob, re.I):
                failed.append((path, name))
    if failed:
        for path, rule in failed:
            print(f"BLOCKED {path}: {rule}")
        return 1
    print(f"PASS: scanned {count} indexed files; no configured secret patterns found")
    return 0

if __name__ == "__main__":
    sys.exit(main())
