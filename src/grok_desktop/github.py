"""GitHub integration for Grok Desktop: a thin, UI-free wrapper around the `git` and `gh` command lines.

Rules (by design):
  * No tokens/passwords are ever read, asked for, stored or logged here. Sign-in is `gh auth login --web`
    (GitHub device flow); gh keeps the credential in its own store. This module never opens gh's/git's credential files.
  * Anything that publishes or changes things remotely (push, create repo, sign out, commit, clone, pull, git init)
    needs `confirmed=True`, which the UI only passes after the user clicked OK in a dialog that showed what will happen.
  * Never a force push, never a delete: `_guard` refuses such arguments no matter who calls.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

HOST = "github.com"
_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
_URL_RE = re.compile(r"https://\S+")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SECRET_NAMES = re.compile(r"(^|/)(\.env(\..*)?|id_(rsa|ed25519|ecdsa|dsa)|.*\.(pem|p12|pfx|key|kdbx)|credentials(\.json)?|\.netrc|hosts\.yml)$",
                           re.IGNORECASE)
_FORBIDDEN = ("--force", "--force-with-lease", "-f", "--delete", "-d", "--mirror", "--prune", "repo delete", "--hard")


class NotConfirmed(Exception):
    pass


class Refused(Exception):
    pass


@dataclass
class Result:
    ok: bool
    out: str = ""
    err: str = ""
    code: int = 0

    @property
    def text(self) -> str:
        return (self.out + ("\n" if self.out and self.err else "") + self.err).strip()


def _guard(args: list[str]) -> None:
    joined = " ".join(args)
    for a in args[1:]:
        if a in ("--force", "--force-with-lease", "-f", "--mirror", "--delete", "--prune", "--hard") or a.startswith("--force") \
                or (args[0] == "git" and len(args) > 1 and args[1] == "push" and a.startswith("+")):
            raise Refused(f"Refused: '{a}' is never allowed here ({joined})")
    if args[:3] == ["gh", "repo", "delete"] or (args[0] == "gh" and "delete" in args[1:3]):
        raise Refused("Refused: deleting repositories is not available from Grok Desktop")
    if args[0] == "git" and len(args) > 1 and args[1] in ("reset", "clean", "rebase", "filter-branch"):
        raise Refused(f"Refused: git {args[1]} is not available from Grok Desktop")


def _env() -> dict:
    e = dict(os.environ)
    e["GIT_TERMINAL_PROMPT"] = "0"       # never hang on a hidden username/password prompt
    e["GH_PROMPT_DISABLED"] = "1"
    e["GIT_ASKPASS"] = e.get("GIT_ASKPASS", "")
    e["NO_COLOR"] = "1"
    e["LC_ALL"] = "C"
    return e


def run(args: list[str], cwd: str | None = None, timeout: float = 60, input_text: str | None = None) -> Result:
    _guard(args)
    exe = shutil.which(args[0])
    if not exe:
        return Result(False, "", f"'{args[0]}' is not installed", 127)
    try:
        p = subprocess.run([exe, *args[1:]], cwd=cwd or None, capture_output=True, text=True, timeout=timeout,
                           env=_env(), input=input_text if input_text is not None else "", errors="replace")
        return Result(p.returncode == 0, p.stdout.strip(), p.stderr.strip(), p.returncode)
    except subprocess.TimeoutExpired:
        return Result(False, "", f"{' '.join(args[:3])} timed out after {int(timeout)} s", 124)
    except OSError as e:
        return Result(False, "", str(e), 126)


def _need(confirmed: bool, what: str) -> None:
    if confirmed is not True:
        raise NotConfirmed(what)


def valid_repo_name(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and name not in (".", "..") and not name.endswith(".git")


def secret_like(paths: list[str]) -> list[str]:
    return [p for p in paths if _SECRET_NAMES.search(p)]


# ----------------------------------------------------------------- status --
def installed() -> dict:
    return {"git": bool(shutil.which("git")), "gh": bool(shutil.which("gh"))}


def auth_status() -> dict:
    """{'gh': bool, 'signed_in': bool, 'user': str, 'detail': str}"""
    if not shutil.which("gh"):
        return {"gh": False, "signed_in": False, "user": "", "detail": "GitHub CLI (gh) is not installed"}
    r = run(["gh", "auth", "status", "--hostname", HOST], timeout=20)
    if not r.ok:
        return {"gh": True, "signed_in": False, "user": "", "detail": "Not signed in"}
    u = run(["gh", "api", "user", "--jq", ".login"], timeout=20)
    user = u.out.strip() if u.ok else ""
    if not user:   # fall back to parsing "Logged in to github.com account NAME"
        m = re.search(r"account (\S+)", r.text)
        user = m.group(1) if m else ""
    return {"gh": True, "signed_in": True, "user": user, "detail": ""}


def repo_info(folder: str) -> dict:
    d = {"folder": folder, "exists": os.path.isdir(folder), "is_repo": False, "branch": "", "remote": "", "remote_url": "",
         "upstream": "", "ahead": 0, "behind": 0, "dirty": [], "has_commits": False, "last": ""}
    if not d["exists"]:
        return d
    if not run(["git", "rev-parse", "--is-inside-work-tree"], cwd=folder).ok:
        return d
    d["is_repo"] = True
    d["branch"] = run(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=folder).out or "(detached)"
    d["has_commits"] = run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=folder).ok
    rem = run(["git", "remote"], cwd=folder).out.split()
    if rem:
        d["remote"] = "origin" if "origin" in rem else rem[0]
        d["remote_url"] = run(["git", "remote", "get-url", d["remote"]], cwd=folder).out
    up = run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=folder)
    if up.ok:
        d["upstream"] = up.out
        c = run(["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd=folder)
        if c.ok and len(c.out.split()) == 2:
            d["ahead"], d["behind"] = (int(x) for x in c.out.split())
    st = run(["git", "status", "--porcelain"], cwd=folder).out
    d["dirty"] = [ln for ln in st.splitlines() if ln.strip()]
    if d["has_commits"]:
        d["last"] = run(["git", "log", "-1", "--pretty=%h %s"], cwd=folder).out
    return d


def dirty_paths(info: dict) -> list[str]:
    out = []
    for ln in info["dirty"]:
        p = ln[3:].strip()
        if " -> " in p:
            p = p.split(" -> ")[-1]
        out.append(p.strip('"'))
    return out


def outgoing_summary(folder: str, info: dict | None = None) -> str:
    info = info or repo_info(folder)
    if not info["has_commits"]:
        return "(no commits yet)"
    if info["upstream"]:
        r = run(["git", "log", "--oneline", "-n", "15", "@{u}..HEAD"], cwd=folder)
        return r.out or "(nothing new to push)"
    return run(["git", "log", "--oneline", "-n", "10"], cwd=folder).out + "\n(new branch: all of these will be published)"


# ---------------------------------------------------------------- actions --
class LoginFlow:
    """`gh auth login --web`: emits the one-time code and URL while gh waits for the browser authorisation.
    We open the browser ourselves (BROWSER is neutered) so the code can be shown in our own window."""

    def __init__(self, on_code: Callable[[str, str], None], on_done: Callable[[Result], None]):
        self.on_code, self.on_done = on_code, on_done
        self.proc: subprocess.Popen | None = None
        self.code = ""

    def start(self) -> None:
        exe = shutil.which("gh")
        if not exe:
            self.on_done(Result(False, "", "GitHub CLI (gh) is not installed", 127))
            return
        env = _env()
        env["BROWSER"] = "true"
        try:
            self.proc = subprocess.Popen([exe, "auth", "login", "--web", "--hostname", HOST, "--git-protocol", "https"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, env=env, errors="replace")
        except OSError as e:
            self.on_done(Result(False, "", str(e), 126))
            return
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        p = self.proc
        buf = ""
        try:
            try:
                p.stdin.write("\n")      # "Press Enter to open github.com in your browser"
                p.stdin.flush()
            except OSError:
                pass
            for line in p.stdout:
                buf += line
                if not self.code:
                    m = _CODE_RE.search(buf)
                    if m:
                        self.code = m.group(1)
                        self.on_code(self.code, "https://github.com/login/device")
        finally:
            rc = p.wait()
            self.on_done(Result(rc == 0, buf.strip(), "" if rc == 0 else "Sign-in did not complete", rc))

    def cancel(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def logout(confirmed: bool = False) -> Result:
    _need(confirmed, "sign out of GitHub")
    return run(["gh", "auth", "logout", "--hostname", HOST], input_text="y\n", timeout=30)


def setup_git_credentials(confirmed: bool = False) -> Result:
    """Lets plain `git push/pull` (also from Grok's shell) use gh's login. Only edits git's config helper line."""
    _need(confirmed, "configure git to use gh")
    return run(["gh", "auth", "setup-git", "--hostname", HOST], timeout=30)


def clone(repo: str, dest_parent: str, confirmed: bool = False) -> Result:
    _need(confirmed, f"clone {repo}")
    repo = repo.strip()
    if not repo or repo.startswith("-") or " " in repo:
        return Result(False, "", "Enter owner/name or a GitHub URL", 2)
    name = repo.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = os.path.join(dest_parent, name)
    if os.path.exists(dest) and os.listdir(dest):
        return Result(False, "", f"{dest} already exists and is not empty", 2)
    os.makedirs(dest_parent, exist_ok=True)
    r = run(["gh", "repo", "clone", repo, dest], timeout=600)
    r.out = r.out or dest
    return r


def pull(folder: str, confirmed: bool = False) -> Result:
    _need(confirmed, "pull")
    return run(["git", "pull", "--ff-only"], cwd=folder, timeout=180)


def commit_all(folder: str, message: str, confirmed: bool = False) -> Result:
    _need(confirmed, "commit")
    message = message.strip()
    if not message:
        return Result(False, "", "Write a commit message first", 2)
    a = run(["git", "add", "-A"], cwd=folder)
    if not a.ok:
        return a
    return run(["git", "commit", "-m", message], cwd=folder)


def push(folder: str, confirmed: bool = False) -> Result:
    """Plain push of the current branch (sets upstream on first push). Never forced."""
    _need(confirmed, "push")
    info = repo_info(folder)
    if not info["is_repo"] or not info["remote"]:
        return Result(False, "", "This folder has no remote yet. Use 'Create repo' first.", 2)
    if not info["has_commits"]:
        return Result(False, "", "Nothing to push: no commits yet.", 2)
    if info["branch"] == "(detached)":
        return Result(False, "", "Detached HEAD: switch to a branch first.", 2)
    if info["upstream"]:
        return run(["git", "push"], cwd=folder, timeout=300)
    return run(["git", "push", "-u", info["remote"], info["branch"]], cwd=folder, timeout=300)


def create_plan(folder: str, name: str, private: bool) -> dict:
    """What 'create repo' would do, for the confirmation dialog (no side effects)."""
    info = repo_info(folder)
    steps = []
    if not info["is_repo"]:
        steps.append("git init (this folder is not a git repository yet)")
    if not info["has_commits"] or info["dirty"]:
        steps.append("git add -A and commit the current files" + ("" if info["has_commits"] else " as 'Initial commit'"))
    steps.append(f"Create {'PRIVATE' if private else 'PUBLIC'} repository '{name}' on your GitHub account")
    steps.append("Add it as remote 'origin' and push")
    files = []
    if os.path.isdir(folder):
        r = run(["git", "ls-files", "-co", "--exclude-standard"], cwd=folder) if info["is_repo"] else \
            Result(True, "\n".join(os.listdir(folder)))
        files = r.out.splitlines()
    return {"info": info, "steps": steps, "files": files, "secrets": secret_like(files)}


def create_repo(folder: str, name: str, private: bool = True, confirmed: bool = False) -> Result:
    _need(confirmed, f"create repo {name}")
    if not valid_repo_name(name):
        return Result(False, "", "Repository names may only contain letters, digits, '.', '-' and '_'", 2)
    if not os.path.isdir(folder):
        return Result(False, "", "Choose an existing project folder first", 2)
    if os.path.realpath(folder) == os.path.realpath(os.path.expanduser("~")):
        return Result(False, "", "Refusing to publish your whole home folder. Pick a project folder.", 2)
    info = repo_info(folder)
    log = []
    if info["remote"]:
        return Result(False, "", f"This folder already has remote '{info['remote']}' ({info['remote_url']})", 2)
    if not info["is_repo"]:
        r = run(["git", "init", "-b", "main"], cwd=folder)
        if not r.ok:
            r = run(["git", "init"], cwd=folder)
        if not r.ok:
            return r
        log.append(r.text)
    if not info["has_commits"] or info["dirty"]:
        run(["git", "add", "-A"], cwd=folder)
        r = run(["git", "commit", "-m", "Initial commit" if not info["has_commits"] else "Update"], cwd=folder)
        if not r.ok and "nothing to commit" not in r.text:
            return Result(False, "\n".join(log), r.text, r.code)
        log.append(r.out)
    r = run(["gh", "repo", "create", name, "--private" if private else "--public", "--source", ".", "--remote", "origin",
             "--push"], cwd=folder, timeout=300)
    r.out = "\n".join(x for x in log + [r.out] if x)
    return r


def repo_web_url(folder: str) -> str:
    r = run(["gh", "repo", "view", "--json", "url", "--jq", ".url"], cwd=folder, timeout=30)
    if r.ok and r.out.startswith("https://"):
        return r.out.strip()
    info = repo_info(folder)
    u = info["remote_url"]
    m = re.match(r"(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?/?$", u)
    return f"https://github.com/{m.group(1)}" if m else ""
