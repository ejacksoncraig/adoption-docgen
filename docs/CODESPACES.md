# Testing it from a link, with GitHub Codespaces

A Codespace is a container GitHub runs for you, built from this repository. It
runs the *actual* application — same engine, same templates, same validation —
and forwards its port so you can open it in a browser tab from anywhere.

Nothing is rewritten for the web. `app/server.py` is the same code that serves
the app on your own machine.

## Use test data only

**A Codespace runs on GitHub's computers, not in the office.** Anything typed
into it has left your control, and generated documents live on a container that
GitHub can recycle at any time.

Use the SAMPLE answers. Real matters belong on the office machine, where the
desktop app writes to a folder you control and nothing crosses a network. This is
a way to *try the software*, not a way to *do the work*.

## Starting one

1. On the repository page: **Code ▾ → Codespaces → Create codespace on main**.
2. Wait for it to build. It installs the dependencies and runs
   `python -m app.cli check`, which validates every template against the field
   schema — if that fails, the app would not have started anyway.
3. In the terminal it opens:

   ```bash
   python -m app.server --no-browser
   ```

4. A toast offers to open the forwarded port; take it, or use the **Ports** tab
   and click the globe beside port 8765.

The tab shows the same interface as the desktop window, including the client-form
import. Generated `.docx` files land in `output/` **inside the Codespace** — use
the file explorer on the left to download one, or right-click → Download.

## Why GitHub Pages cannot do this

Pages serves static files: HTML, CSS, JavaScript, images. It never runs code on a
server. This application's whole engine is Python — it opens `.docx` templates,
validates every variable against `config/fields.json`, renders with Jinja and
writes documents. None of that can happen on Pages.

Publishing to Pages would give you the interface with nothing behind it: every
button would fail.

The only way to genuinely run on Pages is to rewrite the engine in JavaScript so
it works inside the visitor's browser. That is possible — a `.docx` is a zip file
and JavaScript can rebuild one — but it means porting the engine, retokenizing all
nine templates into a different template syntax, and rebuilding the test suite
that currently stops a misspelled variable reaching a filing. It is a project, not
a deployment step.

## PDF export in a Codespace

Not available: LibreOffice is not in the container. The PDF checkbox disables
itself with an explanation, and `.docx` generation is unaffected — which matches
the decision to deliver Word documents and let Word make the PDF.

To try PDF anyway, add to `.devcontainer/devcontainer.json`:

```json
"postCreateCommand": "sudo apt-get update && sudo apt-get install -y libreoffice-writer && pip install --no-cache-dir -r requirements.txt"
```

It adds several hundred megabytes and a few minutes to every rebuild, which is
why it is not there by default.

## Running the tests

```bash
python -m pytest
```

215 tests, and a Codespace is a genuinely useful place to run them: it is Linux,
so it exercises paths that never run on the office's Windows machine.

## Stopping

Codespaces stop themselves after 30 minutes idle, and the free allowance is 60
core-hours a month. Delete one you have finished with from the **Code ▾ →
Codespaces** menu — and delete it if you ever put anything real into it.
