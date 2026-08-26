# Installing the Adoption Filing Generator

For the person receiving the program. Nothing here needs Python, a login, or an
internet connection — the application makes no network request of any kind.

---

## Mac

### 1. Unzip it

Double-click `AdoptionFilingGenerator-macOS.zip`. You get a folder called
`AdoptionFilingGenerator` containing:

```
AdoptionFilingGenerator.app     the program
config/                         settings and the list of adoption types
templates/                      the .docx templates
output/                         generated filings land here
intake/                         saved intake forms land here
```

### 2. Put the whole folder somewhere sensible

Your Documents folder or Applications is fine.

> **Keep the folder together.** Do not drag `AdoptionFilingGenerator.app` out on
> its own. The program looks for `config/` and `templates/` *beside* itself, and
> on its own it will not start. Move the whole `AdoptionFilingGenerator` folder,
> not the app inside it.

> **Not inside iCloud Drive, Google Drive, OneDrive or Dropbox.** The program
> writes finished filings into its own `output/` folder. Put that folder inside
> something that syncs, and every filing — real names, dates of birth, case
> numbers — is copied to a cloud account automatically, as soon as it is
> generated. That is the one thing this program is built not to do: it makes no
> network request of any kind, and nothing it writes leaves the computer unless
> the folder it sits in is sending it somewhere.
>
> A plain folder in Documents does not sync. Check that Desktop & Documents
> syncing is off, or keep it somewhere else entirely.

### 3. The first launch will be refused — this is expected

The program is not signed with an Apple developer certificate, so the first time
you open it macOS says:

> "AdoptionFilingGenerator" cannot be opened because the developer cannot be
> verified.

Nothing is wrong. Apple charges $99/year to make this message go away, and this
is a program written for one office rather than sold. To allow it, once:

1. Double-click the app. Click **Done** or **Cancel** on the warning.
2. Open **System Settings** → **Privacy & Security**.
3. Scroll down to the **Security** section. There is a line saying
   *"AdoptionFilingGenerator" was blocked* with an **Open Anyway** button.
4. Click **Open Anyway**, and confirm with your password or Touch ID.
5. It opens. **macOS remembers** — from now on it is an ordinary double-click.

The exact wording shifts a little between macOS versions. On macOS 14 and
earlier you could instead right-click the app and choose **Open**; on macOS 15
(Sequoia) and later Apple removed that shortcut, and the Privacy & Security
route above is the one that works.

If the button is not there, the Terminal one-liner below does the same thing.
Paste it, press Return, then open the app normally:

```bash
xattr -d com.apple.quarantine /path/to/AdoptionFilingGenerator.app
```

(Drag the app onto the Terminal window instead of typing the path.)

### Which Macs this runs on

Both kinds — Apple Silicon (M1 through M4) and Intel. The build carries both.

---

## Windows

### 1. Unzip it

Right-click the zip → **Extract All**. You get an `AdoptionFilingGenerator`
folder with the same contents as above, but `AdoptionFilingGenerator.exe` in
place of the `.app`.

### 2. Put it near the top of the drive

```
C:\AdoptionFilingGenerator\
```

This matters more on Windows than on a Mac. Windows cannot open a file whose
full path reaches 260 characters, and it is **Word** that refuses, not this
program — so a document generated deep inside a long path can look fine and then
fail to open. Installing under a OneDrive folder spends about 170 of those
characters before the file name even starts.

The program shortens file names to fit and tells you when it has. A short
install path means it never has to.

### 3. The first launch will be warned about — also expected

Windows SmartScreen says *"Windows protected your PC"*. Click **More info** →
**Run anyway**. Once.

---

## Before the first real filing

Open `config/settings.json` in any text editor and fill in the office details:

```json
{
  "attorney_short_name": "",
  "attorney_full_name": "",
  "attorney_oba": "",
  "attorney_firm": "",
  "attorney_address": "",
  "attorney_city_state_zip": "",
  "attorney_phone": "",
  "attorney_email": ""
}
```

These are the same on every filing, which is why they are set once here instead
of being retyped on every intake form. A document that needs them refuses to
generate while they are blank, rather than printing a decree with no attorney of
record.

Updating the program later does **not** overwrite this file, or anything in
`output/` or `intake/`.

---

## Trying it without a real client

On the intake screen, **Fill with test data** invents a complete matter in one
click — every question answered, ready to generate. Press it again for a
different one; the dates, county and yes/no answers all change, so successive
runs produce genuinely different paragraphs rather than the same document with
different names.

Every invented name carries a **SAMPLE** prefix. That is deliberate: what comes
out is otherwise indistinguishable from a real filing sitting in the output
folder.

---

## PDF export is optional

The PDF checkbox needs LibreOffice, which is free from
[libreoffice.org](https://www.libreoffice.org/download/). Without it the
checkbox is greyed out with an explanation, and .docx generation is unaffected.

---

## A note on confidentiality

Everything stays on the computer it runs on. The program makes no network
request, stores nothing in a cloud, and writes only to `output/` and `intake/`
inside its own folder. Generated filings and saved intakes never leave the
machine unless someone moves them.
