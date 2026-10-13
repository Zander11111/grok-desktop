"""GitHub integration tests: real `git` against local bare repos, a fake `gh` on PATH (no network, no real GitHub)."""
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from helpers import app, fake_env, pump, wait_until
from grok_desktop import github as gh

FAKE_GH = r'''#!/usr/bin/env python3
import os, subprocess, sys
a = sys.argv[1:]
open(os.environ["GH_LOG"], "a").write(" ".join(a) + "\n")
state = os.environ["GH_STATE"]
signed = os.path.exists(state)
if a[:2] == ["auth", "status"]:
    print("github.com\n  Logged in to github.com account fakeuser (keyring)" if signed else "You are not logged in")
    sys.exit(0 if signed else 1)
if a[:2] == ["api", "user"]:
    print("fakeuser"); sys.exit(0)
if a[:2] == ["auth", "login"]:
    print("! First copy your one-time code: ABCD-1234")
    print("Press Enter to open github.com in your browser...")
    sys.stdout.flush()
    sys.stdin.readline()
    print("Waiting for authorization")
    open(state, "w").write("1")
    sys.exit(0)
if a[:2] == ["auth", "logout"]:
    os.path.exists(state) and os.remove(state); sys.exit(0)
if a[:2] == ["auth", "setup-git"]:
    sys.exit(0)
if a[:2] == ["repo", "create"]:
    name = a[2]; remote = os.path.join(os.environ["GH_REMOTES"], name + ".git")
    subprocess.check_call(["git", "init", "-q", "--bare", "-b", "main", remote])
    subprocess.check_call(["git", "remote", "add", "origin", remote])
    subprocess.check_call(["git", "push", "-q", "-u", "origin", "HEAD"])
    print("https://github.com/fakeuser/" + name); sys.exit(0)
if a[:2] == ["repo", "clone"]:
    src = os.path.join(os.environ["GH_REMOTES"], a[2].split("/")[-1] + ".git")
    sys.exit(subprocess.call(["git", "clone", "-q", src, a[3]]))
if a[:2] == ["repo", "view"]:
    print("https://github.com/fakeuser/proj"); sys.exit(0)
sys.exit(2)
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    g = bindir / "gh"
    g.write_text(FAKE_GH)
    g.chmod(g.stat().st_mode | stat.S_IEXEC)
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setenv("GH_STATE", str(tmp_path / "signed"))
    monkeypatch.setenv("GH_REMOTES", str(remotes))
    for k, v in (("GIT_AUTHOR_NAME", "T"), ("GIT_AUTHOR_EMAIL", "t@x"), ("GIT_COMMITTER_NAME", "T"), ("GIT_COMMITTER_EMAIL", "t@x")):
        monkeypatch.setenv(k, v)
    return tmp_path


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def ghlog(env):
    p = env / "gh.log"
    return p.read_text() if p.exists() else ""


def test_guard_refuses_force_and_delete():
    for args in (["git", "push", "--force"], ["git", "push", "-f", "origin", "main"], ["git", "push", "origin", "+main"],
                 ["git", "push", "--force-with-lease"], ["gh", "repo", "delete", "x"], ["git", "reset", "--hard"],
                 ["git", "clean", "-fd"], ["git", "push", "--delete", "origin", "b"]):
        with pytest.raises(gh.Refused):
            gh.run(args)


def test_actions_need_confirmation(env, tmp_path):
    for fn, a in ((gh.push, (str(tmp_path),)), (gh.pull, (str(tmp_path),)), (gh.logout, ()), (gh.create_repo, (str(tmp_path), "x", True)),
                  (gh.clone, ("a/b", str(tmp_path))), (gh.commit_all, (str(tmp_path), "m")), (gh.setup_git_credentials, ())):
        with pytest.raises(gh.NotConfirmed):
            fn(*a)
        with pytest.raises(gh.NotConfirmed):
            fn(*a, confirmed="yes")   # only literal True counts
    assert ghlog(env) == ""


def test_auth_status(env):
    s = gh.auth_status()
    assert s["gh"] and not s["signed_in"]
    (env / "signed").write_text("1")
    s = gh.auth_status()
    assert s["signed_in"] and s["user"] == "fakeuser"


def test_no_gh(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert gh.auth_status()["gh"] is False


def test_login_flow_emits_code_and_finishes(env):
    got, done = [], []
    f = gh.LoginFlow(lambda c, u: got.append((c, u)), done.append)
    f.start()
    assert wait_until(lambda: done, 10)
    assert got == [("ABCD-1234", "https://github.com/login/device")]
    assert done[0].ok
    assert "auth login --web --hostname github.com" in ghlog(env)
    assert gh.auth_status()["signed_in"]


def test_create_commit_push_pull_cycle(env):
    proj = env / "proj"
    proj.mkdir()
    (proj / "a.txt").write_text("hi")
    plan = gh.create_plan(str(proj), "proj", True)
    assert any("git init" in s for s in plan["steps"]) and any("PRIVATE" in s for s in plan["steps"]) and not plan["secrets"]
    r = gh.create_repo(str(proj), "proj", True, confirmed=True)
    assert r.ok, r.text
    assert "repo create proj --private --source . --remote origin --push" in ghlog(env)
    info = gh.repo_info(str(proj))
    assert info["is_repo"] and info["remote"] == "origin" and info["upstream"] and info["ahead"] == 0 and not info["dirty"]
    # commit + push
    (proj / "b.txt").write_text("more")
    info = gh.repo_info(str(proj))
    assert gh.dirty_paths(info) == ["b.txt"]
    assert gh.commit_all(str(proj), "add b", confirmed=True).ok
    info = gh.repo_info(str(proj))
    assert info["ahead"] == 1 and "add b" in gh.outgoing_summary(str(proj), info)
    assert gh.push(str(proj), confirmed=True).ok
    remote = str(env / "remotes" / "proj.git")
    assert "add b" in git(remote, "log", "--oneline")
    # pull from another clone
    c2 = env / "c2"
    gh.clone("fakeuser/proj", str(c2), confirmed=True)
    work = str(c2 / "proj")
    (c2 / "proj" / "c.txt").write_text("c")
    git(work, "add", "-A"); git(work, "commit", "-qm", "from c2"); git(work, "push", "-q")
    assert gh.repo_info(str(proj))["behind"] == 0
    git(str(proj), "fetch", "-q")
    assert gh.repo_info(str(proj))["behind"] == 1
    assert gh.pull(str(proj), confirmed=True).ok
    assert (proj / "c.txt").exists()


def test_push_never_forces_and_fails_on_diverged(env):
    proj = env / "p2"; proj.mkdir(); (proj / "a").write_text("1")
    assert gh.create_repo(str(proj), "p2", True, confirmed=True).ok
    work = env / "w"; gh.clone("x/p2", str(work), confirmed=True)
    w = str(work / "p2")
    (work / "p2" / "z").write_text("z"); git(w, "add", "-A"); git(w, "commit", "-qm", "z"); git(w, "push", "-q")
    (proj / "y").write_text("y")
    assert gh.commit_all(str(proj), "y", confirmed=True).ok
    r = gh.push(str(proj), confirmed=True)
    assert not r.ok                       # rejected, not forced
    assert "z" in git(str(env / "remotes" / "p2.git"), "log", "--oneline")
    assert "y" not in git(str(env / "remotes" / "p2.git"), "log", "--oneline").replace("z", "")


def test_create_refusals(env):
    home = os.path.expanduser("~")
    assert not gh.create_repo(home, "x", True, confirmed=True).ok
    assert not gh.create_repo(str(env), "bad name!", True, confirmed=True).ok
    assert not gh.create_repo(str(env / "nope"), "x", True, confirmed=True).ok
    assert "repo create" not in ghlog(env)
    p = env / "withremote"; p.mkdir(); git(str(p), "init", "-q"); git(str(p), "remote", "add", "origin", "https://github.com/a/b.git")
    assert "already has remote" in gh.create_repo(str(p), "x", True, confirmed=True).err


def test_secret_detection(env):
    p = env / "s"; p.mkdir(); (p / ".env").write_text("K=1"); (p / "ok.py").write_text("1")
    plan = gh.create_plan(str(p), "s", True)
    assert plan["secrets"] == [".env"]
    assert gh.secret_like(["a/id_rsa", "x/server.pem", "README.md"]) == ["a/id_rsa", "x/server.pem"]


def test_clone_into_nonempty_refused(env):
    d = env / "dest"; (d / "r").mkdir(parents=True); (d / "r" / "f").write_text("x")
    r = gh.clone("o/r", str(d), confirmed=True)
    assert not r.ok and "not empty" in r.err
    assert not gh.clone("--upload-pack=evil", str(d), confirmed=True).ok


def test_repo_name_validation():
    assert gh.valid_repo_name("my-proj_1.0") and not gh.valid_repo_name("a b") and not gh.valid_repo_name("..") and not gh.valid_repo_name("x.git")


# ------------------------------------------------------------------ UI ------
@pytest.fixture
def dlg(env, tmp_path):
    qa = app()
    fake_env(tmp_path)
    from grok_desktop import paths
    from grok_desktop.github_ui import GitHubDialog

    class Ctl:
        settings = paths.Settings()
    d = GitHubDialog(Ctl())
    d.confirmations = []
    d.answer = True

    def confirm(title, text, ok="OK"):
        d.confirmations.append((title, text, ok))
        return d.answer
    d.confirm = confirm
    d.show()
    yield d
    d.close()


def settle(d):
    pump(0.3)
    assert wait_until(lambda: d.btns["refresh"].isEnabled(), 10)
    pump(0.3)


def test_dialog_status_and_sign_in_out(dlg, env, monkeypatch):
    settle(dlg)
    assert "Not signed in" in dlg.who.text() and dlg.signin_btn.isVisibleTo(dlg)
    opened = []
    from PySide6.QtGui import QDesktopServices
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda u: opened.append(u.toString()) or True))
    dlg.sign_in()
    assert wait_until(lambda: "ABCD-1234" in dlg.code_lbl.text(), 10)
    assert opened == ["https://github.com/login/device"]
    assert wait_until(lambda: "fakeuser" in dlg.who.text(), 10)
    dlg.answer = False
    dlg.sign_out()
    pump(0.3)
    assert gh.auth_status()["signed_in"] and dlg.confirmations[-1][0].startswith("Sign out")
    dlg.answer = True
    dlg.sign_out()
    assert wait_until(lambda: "Not signed in" in dlg.who.text(), 10)


def test_dialog_push_requires_confirm_and_shows_summary(dlg, env):
    (env / "signed").write_text("1")
    proj = env / "proj"; proj.mkdir(); (proj / "a").write_text("1")
    assert gh.create_repo(str(proj), "proj", True, confirmed=True).ok
    (proj / "n").write_text("n"); assert gh.commit_all(str(proj), "newcommit", confirmed=True).ok
    dlg.folder.setText(str(proj)); dlg.refresh(); settle(dlg)
    assert dlg.btns["push"].isEnabled()
    dlg.answer = False
    dlg.do_push(); pump(0.3)
    t, text, ok = dlg.confirmations[-1]
    assert "newcommit" in text and "Branch:" in text and "origin" in text and "never forced" in text and ok == "Push"
    assert gh.repo_info(str(proj))["ahead"] == 1       # declined: nothing pushed
    dlg.answer = True
    dlg.do_push()
    assert wait_until(lambda: gh.repo_info(str(proj))["ahead"] == 0, 10)


def test_dialog_create_declined_does_nothing(dlg, env, monkeypatch):
    (env / "signed").write_text("1")
    proj = env / "newproj"; proj.mkdir(); (proj / "a").write_text("1")
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("newproj", True)))
    # visibility box: pick the first (Private) button instead of exec'ing a modal
    monkeypatch.setattr(QMessageBox, "exec", lambda self: self.setProperty("x", 1) or 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.buttons()[0])
    dlg.folder.setText(str(proj)); dlg.refresh(); settle(dlg)
    dlg.answer = False
    dlg.do_create(); pump(0.3)
    title, text, ok = dlg.confirmations[-1]
    assert "private" in title and "git init" in text and "PRIVATE" in text and ok == "Create and push"
    assert "repo create" not in ghlog(env) and not (proj / ".git").exists()
    dlg.answer = True
    dlg.do_create()
    assert wait_until(lambda: "repo create newproj --private" in ghlog(env), 10)
    assert wait_until(lambda: (proj / ".git").exists(), 5)


def test_main_window_has_github_button(tmp_path):
    qa = app(); fake_env(tmp_path)
    from grok_desktop import theme
    from grok_desktop.app import DesktopApp
    theme.apply(qa)
    d = DesktopApp(qa, show=True)
    try:
        assert d.win.github_btn.text() == "GitHub"
        d.win.open_github()
        assert d.win.github_dialog.isVisible()
        d.win.github_dialog.close()
    finally:
        d.shutdown(); d.win.close()
