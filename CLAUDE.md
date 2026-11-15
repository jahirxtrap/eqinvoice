# EQinvoice

Every electronic voucher of Ecuador's SRI (offline scheme): invoices, credit and debit notes,
purchase settlements, remission guides and withholdings, alone or in a lote. Published on PyPI as
`eqinvoice`. The visible name is **EQinvoice**; everything technical is `eqinvoice`.

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
  model.py        the vouchers in the SRI's terms: Numbered, then Voucher (the priced kinds),
                  RemissionGuide and Withholding (AnyVoucher); PaymentForm, RetentionTax and
                  voucher_number; totals, tax groups, payment forms and withheld values derive here
  protocol.py     every SRI code, rate, name, namespace and address, the tables per kind (schema,
                  root, info tag, party, totals), the XML parser and text_at
  access_key.py   the 49-digit access key, also a lote's, and the issue date on the SRI's clock
  pricing.py      price_line, tax_on and withhold: lines, IVA and retentions to the cent
  sales.py        period_sales: a period's vouchers per IVA rate, as the IVA declaration asks
  document.py     the XML builder of every kind, the XSD check and the RIMPE legends
  signature.py    the .p12 credential (the key that signs, past one that enciphers) and XAdES-BES
  service.py      the SRI's reception and authorization web services, lotes, and SimulatedSri
  registry.py     the SRI's taxpayer registry, SimulatedRegistry and Taxpayer.issuer()
  record.py       an authorized record read back as Issued; read_voucher for a signed document
  errors.py       SriUnavailable, VoucherInvalid, CredentialRefused and its problems
  schemas/        the SRI's XSDs, as published
  labels/es.json  the printed words, as data
  ride/           the printed copy of every kind: labels.py, logo.py (ImageLogo, MarkLogo), pdf.py
src/eqinvoice_mcp/
  settings.py     the only reader of the environment: EQINVOICE_BOOK, EQINVOICE_P12, its password
  book.py         the only module that touches disk: the book's files and the numbering
  desk.py         the decisions: issuer, parties, drafts of every kind with their preview,
                  issuing, checking
  model.py        Profile, Draft (the voucher, numbered when issued), LegacyDraft (1.1's), Previewed,
                  Filed, Standing, SalesReport
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
   - An SRI code, rate or name lives in `protocol.py`; the payment, retention tax and subject
     tables are `PaymentForm`, `RetentionTax` and `SubjectType` in `model.py`, whose values are
     the SRI's codes. A schema's version is read from its file name.
   - A voucher's number is written by `voucher_number`, and its sequential by `sequential_code`.
   - A total is derived in `model.py` (`subtotal`, `vat`, `total`, `tax_groups`, `payment_forms`),
     and the XML, the RIDE and the MCP read it from there.
   - The printed words live in `labels/`, and the MCP's tool descriptions read them too.
   - What the SRI's registry holds (a legal name, an address, the accounting duty, the regime and
     its RIMPE category, a trade name) is looked up, never typed in.
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
   new entry in `SCHEMAS`. Where the SRI's ficha técnica asks for more than its published XSD,
   the check adds it in memory (`_schema` admits the popular business legend of annex 22), and
   the files stay as published.
8. **No secrets and no real taxpayers in the repo.** Tests use made-up RUCs, signatures are
   simulated, and the live scripts take the .p12 from the environment.

## Extending

- **A new voucher kind**: one more `VoucherKind`; its entries in `protocol.py` (`SCHEMAS`,
  `DOCUMENT_CODES`, `ROOTS`, `INFO`, and `PARTIES`, `CODE_TAGS`, `TOTAL_TAGS` or `TAX_TOTALS`
  where they apply); a type on `Numbered` if `Voucher` does not fit it; its branches in
  `document.build` and `record._voucher`; its labels and its RIDE body; a desk draft and a tool;
  and `a_document` in the tests' builders, which puts it through the round trip of
  `test_record`. The book gives it its own series on its own.
- **A new label language**: a new JSON file of the same shape; no code change.
- **A new logo kind**: a class with `flowable(height, one_ink) -> Flowable`; `render` takes any `Logo`.
- **A new tool**: a method on `Desk` with its decision, then a thin `@tool(<hints>)` in `server.py`
  that only translates arguments; its anticipated failures join `ANTICIPATED`.
