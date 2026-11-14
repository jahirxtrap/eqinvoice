# EQinvoice

Electronic invoices and credit notes for Ecuador's SRI (offline scheme, version 1.1.0), published
on PyPI as `eqinvoice`. The visible name is **EQinvoice**; everything technical is `eqinvoice`.

**One repository, three ways to use it:**

- **`src/eqinvoice/`**: the library. It holds no storage, no configuration and no web framework:
  values in, documents out. Any system (a shop, an ERP) builds on it.
- **`src/eqinvoice_mcp/`**: the MCP server (`eqinvoice-mcp`, extra `[mcp]`) for one issuer. It owns
  a folder, the book, and is the only part that reads the environment or touches disk.
- **`plugin/`** with **`.claude-plugin/marketplace.json`**: the server as a Claude Code plugin, in
  its own marketplace.

The library never imports the MCP package; the MCP package only uses the library's public names.

## Architecture

```
[a system] ──values──> [eqinvoice] ──signed XML──> [SRI reception] ──> [SRI authorization]
                            │
                            └──> [SRI registry]  (who holds a RUC, its establishments)

[assistant] ──stdio──> [eqinvoice-mcp] ──> eqinvoice
                            └──> the book: profile.json, clients/, drafts/, vouchers/, brand/
```

## Layout

```
src/eqinvoice/
  model.py        the voucher in the SRI's terms; totals, tax groups and payment forms derive here
  protocol.py     every SRI code, rate, name, namespace and address, the XML parser and text_at
  access_key.py   the 49-digit access key and the issue date on the SRI's clock
  pricing.py      price_line: a line's subtotal and IVA to the cent
  document.py     the XML builder, the XSD check and the RIMPE legend
  signature.py    the .p12 credential and the XAdES-BES signature
  service.py      the SRI's reception and authorization web services, and SimulatedSri
  registry.py     the SRI's taxpayer registry, SimulatedRegistry and Taxpayer.issuer()
  record.py       an authorized record read back as Issued; read_voucher for a signed document
  errors.py       SriUnavailable, VoucherInvalid, CredentialRefused and its problems
  schemas/        the SRI's XSDs, as published
  labels/es.json  the printed words, as data
  ride/           the printed copy: labels.py, logo.py (ImageLogo, MarkLogo), pdf.py (render)
src/eqinvoice_mcp/
  settings.py     the only reader of the environment: EQINVOICE_BOOK, EQINVOICE_P12, its password
  book.py         the only module that touches disk: the book's files and the numbering
  desk.py         the decisions: issuer, clients, drafts with their preview, issuing, checking
  model.py        Profile, Draft, Previewed, Filed, Standing
  server.py       the tools, their hints, and anticipated failures turned into ToolError
  icon.svg        the server's icon
plugin/           .claude-plugin/plugin.json (with its userConfig) and .mcp.json (uvx from PyPI)
tests/            one test_<capability>.py per module; builders.py holds the shared builders
scripts/          smoke_sri.py and smoke_registry.py, the live round trips
.github/          ci.yml (checks and plugin validation), release.yml (PyPI and the release), assets/
```

## Version contract

The version is written in four places that must agree, and `tests/test_plugin.py` fails when they
do not: `pyproject.toml`, `plugin/.claude-plugin/plugin.json`, its entry in
`.claude-plugin/marketplace.json`, and the `eqinvoice[mcp]==<version>` that `plugin/.mcp.json`
launches. The plugin's description and keywords are the same in the manifest and the marketplace.

A release is one commit named `v<x.y.z>` that bumps those four and replaces `CHANGELOG.md` (no
headers, versions or dates; one bullet per change, as what a user sees). Its tag is unprefixed
(`1.2.0`): `release.yml` checks, builds and publishes to PyPI through trusted publishing, skipping
a version PyPI already has, and opens the GitHub release with `CHANGELOG.md`.

## Development

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/mypy
.venv/bin/pytest
claude plugin validate . && claude plugin validate plugin
```

A change to signing, the XML, the web services or the registry is also proven live with the
scripts in `scripts/`.

## Hard rules

1. **English everywhere** except the data the SRI itself fixes: its XML tags, codes and the
   Spanish printed labels.
2. **No comments.** Self-explanatory names. A one-line descriptive docstring only where the code
   cannot say it (a contract, a unit, a behaviour that is not obvious), never on a module and never
   to repeat a name. The MCP tools keep theirs: they are the descriptions the client reads.
3. **One home per fact.**
   - An SRI code, rate or name lives in `protocol.py`.
   - A total is derived in `model.py` (`subtotal`, `vat`, `total`, `tax_groups`, `payment_forms`),
     and the XML, the RIDE and the MCP read it from there.
   - The printed words live in `labels/`, and the MCP's tool descriptions read them too.
   - What the SRI's registry holds (a legal name, an address, the accounting duty) is looked up,
     never typed in.
4. **The library reads no configuration.** The caller passes the environment, the credential,
   the timeout and the logo. Only `eqinvoice_mcp/settings.py` reads environment variables.
5. **Fail with a reason.** Every failure raises one of the errors in `errors.py` (or the desk's
   `Refused` and the book's `NotInBook`), and a caller can tell retry later (`SriUnavailable`) from
   fix the data (`VoucherInvalid`) from fix the signature (`CredentialRefused`). The server hands
   them to the client as `ToolError`, or the SDK would hide the reason.
6. **Nothing reaches the SRI without approval.** A voucher starts as a draft with a printed
   preview; only `issue_draft` signs and sends it, and the server's instructions ask for the user's
   approval first. A number is taken for good once signed, per environment, kind, establishment
   and point.
7. **The schemas are the SRI's.** Never edit the XSDs. A new schema version is a new file and a
   new entry in `SCHEMAS`.
8. **No secrets and no real taxpayers in the repo.** Tests use made-up RUCs, signatures are
   simulated, and the live scripts take the .p12 from the environment.

## Extending

- **A new voucher kind**: one more `VoucherKind`, its entries in `protocol.py` (document code, root,
  info tag, schema, code tag), the builder and reader branches, its labels and its tests. The book
  gives it its own series on its own.
- **A new label language**: a new JSON file of the same shape; no code change.
- **A new logo kind**: a class with `flowable(height, one_ink) -> Flowable`; `render` takes any `Logo`.
- **A new tool**: a method on `Desk` with its decision, then a thin `@tool(<hints>)` in `server.py`
  that only translates arguments; its anticipated failures join `ANTICIPATED`.
