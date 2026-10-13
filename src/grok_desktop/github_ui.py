"""GitHub dialog for Grok Desktop (non-modal). All remote/changing actions go through `confirm()` first."""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)

from . import github as gh


class _Bus(QObject):
    done = Signal(object, object)      # (callable, result)


class GitHubDialog(QDialog):
    codeReady = Signal(str, str)
    loginDone = Signal(object)

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("GitHub")
        self.setModal(False)
        self.resize(640, 640)
        self.flow: gh.LoginFlow | None = None
        self.auth: dict = {}
        self.info: dict = {}
        self._bus = _Bus()
        self._bus.done.connect(lambda fn, res: fn(res))
        self.codeReady.connect(self._show_code)
        self.loginDone.connect(self._login_done)

        v = QVBoxLayout(self)
        self.who = QLabel()
        self.who.setTextFormat(Qt.RichText)
        v.addWidget(self.who)
        r = QHBoxLayout()
        self.signin_btn = QPushButton("Sign in to GitHub")
        self.signin_btn.clicked.connect(self.sign_in)
        self.signout_btn = QPushButton("Sign out")
        self.signout_btn.clicked.connect(self.sign_out)
        self.gitcred_btn = QPushButton("Let git use this login")
        self.gitcred_btn.setToolTip("Runs 'gh auth setup-git' so plain git push/pull (also inside Grok's shell) works")
        self.gitcred_btn.clicked.connect(self.setup_git)
        for b in (self.signin_btn, self.signout_btn, self.gitcred_btn):
            r.addWidget(b)
        r.addStretch()
        v.addLayout(r)
        self.code_lbl = QLabel()
        self.code_lbl.setTextFormat(Qt.RichText)
        self.code_lbl.setWordWrap(True)
        self.code_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.code_lbl.hide()
        v.addWidget(self.code_lbl)

        f = QHBoxLayout()
        f.addWidget(QLabel("Project folder:"))
        self.folder = QLineEdit(ctl.settings.get("github_folder", ""))
        self.folder.setPlaceholderText("Folder Grok is working in (a git repo, or a folder to publish)")
        self.folder.editingFinished.connect(self.refresh)
        f.addWidget(self.folder, 1)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self.pick_folder)
        f.addWidget(pick)
        v.addLayout(f)
        self.repo_lbl = QLabel()
        self.repo_lbl.setTextFormat(Qt.RichText)
        self.repo_lbl.setWordWrap(True)
        v.addWidget(self.repo_lbl)

        r2 = QHBoxLayout()
        self.btns = {}
        for key, label, fn in (("pull", "Pull", self.do_pull), ("commit", "Commit…", self.do_commit),
                               ("push", "Push…", self.do_push), ("create", "Create repo…", self.do_create),
                               ("web", "Open on GitHub", self.do_web), ("refresh", "Refresh", self.refresh)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, fn=fn: fn())
            self.btns[key] = b
            r2.addWidget(b)
        v.addLayout(r2)
        r3 = QHBoxLayout()
        self.clone_edit = QLineEdit()
        self.clone_edit.setPlaceholderText("Clone: owner/name or https://github.com/owner/name")
        cb = QPushButton("Clone…")
        cb.clicked.connect(self.do_clone)
        self.btns["clone"] = cb
        r3.addWidget(self.clone_edit, 1)
        r3.addWidget(cb)
        v.addLayout(r3)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        note = QLabel("Sign-in uses GitHub's device flow through the 'gh' command; Grok Desktop never sees or stores your password or token. "
                      "Nothing is pushed, created or signed out without you confirming. Force pushes and deletes are not available.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        QTimer.singleShot(0, self.refresh)

    # -------------------------------------------------------------- helpers --
    def confirm(self, title: str, text: str, ok: str = "OK") -> bool:
        """The ONLY gate for changing actions. Tests replace this."""
        m = QMessageBox(QMessageBox.Question, title, text, QMessageBox.NoButton, self)
        yes = m.addButton(ok, QMessageBox.AcceptRole)
        m.addButton("Cancel", QMessageBox.RejectRole)
        m.setDefaultButton(m.buttons()[-1])
        m.exec()
        return m.clickedButton() is yes

    def say(self, text: str) -> None:
        self.log.appendPlainText(text)

    def folder_path(self) -> str:
        return os.path.expanduser(self.folder.text().strip())

    def bg(self, work, then) -> None:
        def run():
            try:
                res = work()
            except Exception as e:      # NotConfirmed/Refused/etc. become a visible error, never a crash
                res = gh.Result(False, "", f"{type(e).__name__}: {e}", 1)
            self._bus.done.emit(then, res)
        threading.Thread(target=run, daemon=True).start()

    def _busy(self, on: bool) -> None:
        for b in self.btns.values():
            b.setEnabled(not on)

    def _report(self, label: str):
        def cb(res):
            self._busy(False)
            self.say(f"{'✓' if res.ok else '✗'} {label}" + (f"\n{res.text}" if res.text else ""))
            self.refresh()
        return cb

    def _act(self, label: str, work) -> None:
        self._busy(True)
        self.say(f"… {label}")
        self.bg(work, self._report(label))

    # --------------------------------------------------------------- status --
    def refresh(self, *_a) -> None:
        folder = self.folder_path()
        if folder:
            self.ctl.settings.set("github_folder", folder)

        def work():
            return gh.auth_status(), (gh.repo_info(folder) if folder else None)
        self.bg(work, self._apply_status)

    def _apply_status(self, res) -> None:
        if isinstance(res, gh.Result):
            self.say(res.text)
            return
        self.auth, self.info = res
        a = self.auth
        if not a["gh"]:
            self.who.setText("<b>GitHub CLI (gh) is not installed.</b> Install it (sudo apt install gh) and reopen this window.")
        elif a["signed_in"]:
            self.who.setText(f"Signed in to GitHub as <b>{a['user'] or '?'}</b>")
        else:
            self.who.setText("Not signed in to GitHub.")
        self.signin_btn.setVisible(a["gh"] and not a["signed_in"])
        self.signout_btn.setVisible(a["signed_in"])
        self.gitcred_btn.setVisible(a["signed_in"])
        i = self.info
        if not i:
            self.repo_lbl.setText("Choose a project folder.")
        elif not i["exists"]:
            self.repo_lbl.setText("That folder does not exist.")
        elif not i["is_repo"]:
            self.repo_lbl.setText("Not a git repository yet. 'Create repo…' will set one up and publish it (after you confirm).")
        else:
            bits = [f"Branch <b>{i['branch']}</b>"]
            bits.append(f"remote <b>{i['remote']}</b> {i['remote_url']}" if i["remote"] else "no remote")
            if i["upstream"]:
                bits.append(f"{i['ahead']} ahead / {i['behind']} behind")
            bits.append(f"{len(i['dirty'])} uncommitted change(s)" if i["dirty"] else "clean")
            if i["last"]:
                bits.append(f"last: {i['last']}")
            self.repo_lbl.setText("; ".join(bits))
        ok_repo = bool(i and i["is_repo"])
        signed = bool(a.get("signed_in"))
        self.btns["pull"].setEnabled(ok_repo and bool(i["upstream"]))
        self.btns["commit"].setEnabled(ok_repo and bool(i["dirty"]))
        self.btns["push"].setEnabled(ok_repo and bool(i["remote"]) and i["has_commits"])
        self.btns["create"].setEnabled(signed and bool(i and i["exists"]) and not (i and i["remote"]))
        self.btns["web"].setEnabled(ok_repo and bool(i["remote"]))
        self.btns["clone"].setEnabled(signed)

    def pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Project folder", self.folder_path() or os.path.expanduser("~"))
        if d:
            self.folder.setText(d)
            self.refresh()

    # ---------------------------------------------------------------- auth --
    def sign_in(self) -> None:
        self.code_lbl.setText("Starting GitHub sign-in…")
        self.code_lbl.show()
        self.signin_btn.setEnabled(False)
        self.flow = gh.LoginFlow(lambda c, u: self.codeReady.emit(c, u), lambda r: self.loginDone.emit(r))
        self.flow.start()

    def _show_code(self, code: str, url: str) -> None:
        self.code_lbl.setText(f"1. Your one-time code: <b style='font-size:20px'>{code}</b> (copied to the clipboard)<br>"
                              f"2. Enter it at <a href='{url}'>{url}</a> (opened in your browser) and approve.<br>"
                              "This window updates when GitHub confirms.")
        self.code_lbl.setOpenExternalLinks(True)
        try:
            QGuiApplication.clipboard().setText(code)
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl(url))

    def _login_done(self, res) -> None:
        self.signin_btn.setEnabled(True)
        self.code_lbl.hide()
        self.say(("✓ Signed in to GitHub" if res.ok else "✗ GitHub sign-in did not complete"))
        self.refresh()

    def sign_out(self) -> None:
        if self.confirm("Sign out of GitHub?", f"Sign out {self.auth.get('user', '')} from GitHub on this computer?\n"
                        "Grok and git will lose access until you sign in again.", "Sign out"):
            self._act("Sign out", lambda: gh.logout(confirmed=True))

    def setup_git(self) -> None:
        if self.confirm("Let git use your GitHub login?",
                        "This runs 'gh auth setup-git', which adds gh as git's credential helper for github.com in your git config "
                        "so 'git push' and 'git pull' work (also when Grok runs them). No token is copied.", "Do it"):
            self._act("Configure git to use gh", lambda: gh.setup_git_credentials(confirmed=True))

    # ------------------------------------------------------------- actions --
    def _need_folder(self) -> str | None:
        f = self.folder_path()
        if not f or not os.path.isdir(f):
            QMessageBox.information(self, "GitHub", "Choose an existing project folder first.")
            return None
        return f

    def do_pull(self) -> None:
        f = self._need_folder()
        if f and self.confirm("Pull?", f"Fetch and fast-forward '{self.info.get('branch')}' from {self.info.get('upstream')}?\n"
                              "(Never merges or rewrites your work; fails if it cannot fast-forward.)", "Pull"):
            self._act("Pull", lambda: gh.pull(f, confirmed=True))

    def do_commit(self) -> None:
        f = self._need_folder()
        if not f or not self.info.get("dirty"):
            return
        paths = gh.dirty_paths(self.info)
        sec = gh.secret_like(paths)
        listing = "\n".join(self.info["dirty"][:25]) + ("\n…" if len(self.info["dirty"]) > 25 else "")
        if sec:
            QMessageBox.warning(self, "Possible secrets", "These files look like secrets or keys and will NOT be committed "
                                "until you remove them or add them to .gitignore:\n\n" + "\n".join(sec))
            return
        msg, ok = QInputDialog.getText(self, "Commit", f"Commit message ({len(self.info['dirty'])} changed file(s)):")
        if not ok or not msg.strip():
            return
        if self.confirm("Commit all changes?", f"Branch {self.info['branch']}\nMessage: {msg.strip()}\n\nWill stage and commit:\n{listing}", "Commit"):
            self._act("Commit", lambda: gh.commit_all(f, msg, confirmed=True))

    def do_push(self) -> None:
        f = self._need_folder()
        if not f:
            return
        info = gh.repo_info(f)
        if info["dirty"]:
            self.say("Note: you have uncommitted changes; they will NOT be pushed (use Commit first).")
        summary = gh.outgoing_summary(f, info)
        target = info["upstream"] or f"{info['remote']}/{info['branch']} (new upstream)"
        if self.confirm("Push to GitHub?", f"Branch: {info['branch']}\nRemote: {info['remote']}  {info['remote_url']}\nTo: {target}\n\n"
                        f"Commits that will be pushed:\n{summary}\n\nThis is a normal push (never forced).", "Push"):
            self._act("Push", lambda: gh.push(f, confirmed=True))

    def do_create(self) -> None:
        f = self._need_folder()
        if not f:
            return
        name, ok = QInputDialog.getText(self, "Create GitHub repository", "Repository name:", text=os.path.basename(f.rstrip("/")))
        name = name.strip()
        if not ok:
            return
        if not gh.valid_repo_name(name):
            QMessageBox.warning(self, "GitHub", "Names may only contain letters, digits, '.', '-' and '_'.")
            return
        r = QMessageBox(QMessageBox.Question, "Visibility", f"Who can see '{name}'?", QMessageBox.NoButton, self)
        priv = r.addButton("Private (recommended)", QMessageBox.AcceptRole)
        pub = r.addButton("Public", QMessageBox.ActionRole)
        r.addButton("Cancel", QMessageBox.RejectRole)
        r.setDefaultButton(priv)
        r.exec()
        if r.clickedButton() not in (priv, pub):
            return
        private = r.clickedButton() is priv
        plan = gh.create_plan(f, name, private)
        text = (f"Folder: {f}\n\n" + "\n".join(f"  • {s}" for s in plan["steps"]) +
                f"\n\n{len(plan['files'])} file(s) will be published, e.g.:\n" + "\n".join(plan["files"][:15]))
        if plan["secrets"]:
            QMessageBox.warning(self, "Possible secrets", "These look like secrets/keys. Add them to .gitignore or remove them, then try again:\n\n"
                                + "\n".join(plan["secrets"][:20]))
            return
        if not private:
            text += "\n\nPUBLIC: anyone on the internet will be able to read these files."
        if self.confirm(f"Create {'private' if private else 'PUBLIC'} repo '{name}'?", text, "Create and push"):
            self._act(f"Create repo {name}", lambda: gh.create_repo(f, name, private, confirmed=True))

    def do_web(self) -> None:
        f = self._need_folder()
        if f:
            def done(url):
                if url:
                    QDesktopServices.openUrl(QUrl(url))
                else:
                    self.say("✗ Could not work out the GitHub URL for this folder.")
            self.bg(lambda: gh.repo_web_url(f), done)

    def do_clone(self) -> None:
        repo = self.clone_edit.text().strip()
        if not repo:
            return
        parent = QFileDialog.getExistingDirectory(self, "Clone into which folder?", os.path.expanduser("~"))
        if parent and self.confirm("Clone repository?", f"Clone {repo}\ninto {parent}/{repo.rstrip('/').split('/')[-1].removesuffix('.git')}", "Clone"):
            def after(res):
                self._report("Clone")(res)
                if res.ok:
                    self.folder.setText(res.out.splitlines()[-1] if res.out else self.folder.text())
                    self.refresh()
            self._busy(True)
            self.say(f"… Clone {repo}")
            self.bg(lambda: gh.clone(repo, parent, confirmed=True), after)

    def closeEvent(self, e):
        if self.flow:
            self.flow.cancel()
        super().closeEvent(e)
