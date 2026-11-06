# ecuador-einvoicing

A library for the SRI's offline electronic vouchers (factura and nota de crédito, version 1.1.0). It holds no storage, no configuration and no web framework: values in, documents out.

## Layout

```
src/ecuador_einvoicing/
  model.py        the voucher in the SRI's terms; totals, tax groups and payment forms derive here
  protocol.py     every SRI code, name, namespace and address, the XML parser and text_at
  access_key.py   the 49-digit access key and the issue date on the SRI's clock
  document.py     the XML builder, the XSD check and the RIMPE legend
  signature.py    the .p12 credential and the XAdES-BES signature
  service.py      the SRI's reception and authorization web services, and SimulatedSri
  record.py       an authorized record read back as an Issued voucher
  errors.py       SriUnavailable, VoucherInvalid, CredentialRefused and its problems
  schemas/        the SRI's XSDs, as published
  labels/es.json  the printed words, as data
  ride/           the printed copy: labels.py, logo.py (ImageLogo, MarkLogo), pdf.py (render)
tests/            one test_<capability>.py per module; builders.py holds the shared builders
scripts/          smoke_sri.py, the live round trip against the SRI's test environment
```

## Hard rules

- **English everywhere** except the data the SRI itself fixes: its XML tags, codes and the Spanish printed labels.
- **No comments.** Self-explanatory names; at most a one-line descriptive docstring.
- **One home per fact.**
  - An SRI code or name lives in `protocol.py`.
  - A total is derived in `model.py` (`subtotal`, `vat`, `total`, `tax_groups`, `payment_forms`), and both the XML and the RIDE read it from there.
  - The printed words live in `labels/`.
- **No configuration is read here.** The caller passes the environment, the credential, the timeout and the logo. The library never touches environment variables or files it was not given.
- **Fail with a reason.** Every failure raises one of the errors in `errors.py`, and a caller can tell retry later (`SriUnavailable`) from fix the data (`VoucherInvalid`) from fix the signature (`CredentialRefused`).
- **The schemas are the SRI's.** Never edit the XSDs. A new schema version is a new file and a new entry in `SCHEMAS`.

## Extending

- **A new voucher kind**: one more `VoucherKind`, its entries in `protocol.py` (document code, root, info tag, schema, code tag), the builder and reader branches, its labels and its tests.
- **A new label language**: a new JSON file of the same shape; no code change.
- **A new logo kind**: a class with `flowable(height, one_ink) -> Flowable`; `render` takes any `Logo`.

## Before calling it done

```bash
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/mypy
.venv/bin/pytest
```

A change to signing, the XML or the web services is also proven live with `scripts/smoke_sri.py` against the test environment.