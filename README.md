<p align="center">
  <img src="https://raw.githubusercontent.com/jahirxtrap/eqinvoice/main/.github/assets/banner.png" alt="EQinvoice" width="760">
</p>

Every electronic voucher of **Ecuador's SRI** (offline scheme): invoices, credit and debit notes,
purchase settlements, remission guides and withholdings. Build the voucher, sign it XAdES-BES with
the issuer's .p12, send it alone or in a lote, read the authorization back and print the RIDE. Use
it as a **library** inside your own system, or as an **MCP server** that lets an assistant draft,
preview, issue and follow one issuer's vouchers.

<p align="center">
  <a href="https://pypi.org/project/eqinvoice/">PyPI</a> ·
  <a href="https://github.com/jahirxtrap/eqinvoice#library">Library</a> ·
  <a href="https://github.com/jahirxtrap/eqinvoice#claude-code-plugin">Claude Code plugin</a> ·
  <a href="https://github.com/jahirxtrap/eqinvoice#mcp-server">MCP server</a>
</p>

```
[your system] ──values──> [eqinvoice] ──signed XML──> [SRI reception] ──> [SRI authorization]
[assistant]   ──MCP────> [eqinvoice-mcp] ──> the book (clients, drafts, vouchers)
```

---

## Install

```bash
pip install eqinvoice           # the library
pip install "eqinvoice[mcp]"    # with the MCP server
```

Python 3.11 or newer. In Claude Code, the [plugin](https://github.com/jahirxtrap/eqinvoice#claude-code-plugin) installs everything in one step.

## Capabilities

- **Vouchers**: invoices (factura 1.1.0), credit notes (nota de crédito 1.1.0), debit notes (nota de débito 1.0.0), purchase settlements (liquidación de compra 1.1.0), remission guides (guía de remisión 1.1.0) and withholdings (comprobante de retención 2.0.0), checked against the SRI's own XSD before they leave.
- **Lote**: up to 50 signed vouchers sent at once under their own access key, with each one's authorization read back.
- **Access key**: the 49-digit clave de acceso with its modulo 11 check digit, and the issue date on the SRI's clock.
- **Signature**: XAdES-BES with the issuer's .p12 and its password, refused with a reason when it is missing, unreadable, out of date or not for signing. A .p12 holding a key to encipher besides the one to sign, as the Banco Central del Ecuador issues them, signs with the key whose certificate is for digital signature.
- **SRI web services**: reception and authorization, in the test environment (celcer) or in production (cel).
- **Taxpayer registry**: a RUC looked up in the SRI's public registry (legal name, standing, regime and RIMPE category, accounting duty, establishments and their trade names), and the issuer its vouchers name, with the legend of its regime.
- **SRI catalogs**: the payment forms as `PaymentForm`, the IVA rates by their codes, and voucher numbers written as the SRI prints them.
- **Priced lines**: a line's subtotal and IVA worked out to the cent from its quantity, unit price, discount and the SRI's rate code.
- **Retentions**: a withholding's values worked out to the cent, IVA retentions checked against the SRI's table of codes.
- **Period sales**: the vouchers of a month, or any period, added up per IVA rate as the IVA declaration asks for them, debit notes adding to the invoices and credit notes offsetting them.
- **Printed copy (RIDE)**: an A4 page or an 80 mm strip, with the SRI's full block of totals and the access key as a barcode (page) or a QR code (strip).
- **Logo**: a picture, or a mark and a name drawn as vectors, set beside each other as a lockup.
- **Labels**: the printed words are data. Spanish by default, as the SRI prints them; any file of the same shape replaces them.
- **Simulation**: a simulated signature, SRI and registry that answer on the spot, for development and tests.

---

## Library

```python
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from eqinvoice import (
    Buyer,
    BuyerKind,
    Environment,
    Issuer,
    Payment,
    PaymentForm,
    SriWebServices,
    Voucher,
    VoucherKind,
    access_key,
    issue_date,
    price_line,
    read,
    read_credential,
    sign_voucher,
)
from eqinvoice.ride import ImageLogo, Layout, render

issuer = Issuer(
    legal_name="ACME COMERCIAL S.A.S.",
    ruc="1790011223001",
    head_office_address="AV. AMAZONAS N10-20, QUITO",
    keeps_accounts=True,
)
credential = read_credential(Path("signature.p12"), "p12 password")
now = datetime.now(UTC)
issued_on = issue_date(now)
line = price_line("SKU-1", "Cafe molido (500 g)", Decimal("1"), Decimal("10.00"), "4")
voucher = Voucher(
    kind=VoucherKind.INVOICE,
    environment=Environment.TEST,
    access_key=access_key(
        issued_on, VoucherKind.INVOICE, issuer.ruc, Environment.TEST, "001", "001", 1
    ),
    establishment="001",
    point="001",
    sequential=1,
    establishment_address="AV. AMAZONAS N10-20, QUITO",
    issued_on=issued_on,
    buyer=Buyer(BuyerKind.CEDULA, "1710034065", "Ana Perez", "ana@example.com"),
    lines=(line,),
    payments=(Payment(PaymentForm.WITHOUT_FINANCIAL_SYSTEM, Decimal("11.50")),),
)

document = sign_voucher(voucher, issuer, credential, now)
sri = SriWebServices(Environment.TEST)
reception = sri.send(document)
answer = sri.authorize(voucher.access_key)

if answer is not None and answer.authorized:
    issued = read(answer.record)
    page = render(issued, Layout.PAGE, logo=ImageLogo(Path("logo.png")))
    strip = render(issued, Layout.TICKET, logo=ImageLogo(Path("logo.png")))
```

- **What to keep**: `answer.record` is the authorized XML, the voucher with tax validity. Store it: `read(record)` gives the voucher back and `render` prints it whenever it is needed.
- **Still processing**: `authorize` gives `None` while the SRI has not answered yet; ask again later with the same access key.
- **Sent twice**: `reception.already_there` is true when the SRI already holds that access key, so its answer waits at the authorization service.
- **Numbering**: the establishment, the emission point and the sequential belong to the caller's system. A sequential is never reused, and a voucher sent again keeps its access key.
- **Signed documents**: `read_voucher(document)` gives back the voucher a signed or unsigned document carries, of any kind.
- **Declaring**: `period_sales(vouchers, since, until)` adds up the authorized vouchers issued between both days per IVA rate: `gross` is the invoiced base, `net` that base less the credit notes and `vat` its IVA, the three columns of the monthly IVA declaration. Debit notes add up like invoices; settlements, guides and withholdings are no sales. Pass only what the SRI authorized.

### Every kind

`Voucher` is every priced kind; `RemissionGuide` and `Withholding` carry their own shape. The three
share the numbering of `Numbered` (kind, environment, access key, establishment, point, sequential,
establishment address, issue date), and `sign_voucher`, `read`, `read_voucher` and `render` take
any of them (`AnyVoucher`).

| Kind | Type | What it carries |
|---|---|---|
| `INVOICE` | `Voucher` | `buyer`, `lines`, `payments` |
| `CREDIT_NOTE` | `Voucher` | `buyer`, `lines`, `modified` (the invoice it credits and why) |
| `DEBIT_NOTE` | `Voucher` | `buyer`, `lines` as its charges (each description is a reason, each line keeps its IVA rate), `modified`, `payments` |
| `PURCHASE_SETTLEMENT` | `Voucher` | `buyer` as the supplier (with `address`), `lines`, `payments` |
| `REMISSION_GUIDE` | `RemissionGuide` | `carrier`, `plate`, `departure`, `starts_on`, `ends_on`, `recipients` (each with its `items` and the `Reference` it carries) |
| `WITHHOLDING` | `Withholding` | `subject`, `fiscal_period`, `documents`: each `SupportDocument` with its `taxes`, `retentions` and `payments` |

```python
from eqinvoice import RetentionTax, tax_on, withhold

tax_on(Decimal("100"), "4")                                      # IVA 15 % over 100: 15.00
withhold(RetentionTax.INCOME, "303", Decimal("100"), Decimal("10"))  # income tax, its percentage
withhold(RetentionTax.VAT, "1", Decimal("15"))                       # IVA: code 1 is 30 %
```

A final consumer can only buy: every other kind names its party. A foreign party is
`BuyerKind.FOREIGN`; a withholding over one also says whether it is a person or a company
(`SubjectType`).

### Lote

```python
from eqinvoice import batch

lote_key = access_key(issued_on, VoucherKind.INVOICE, issuer.ruc, Environment.TEST, "001", "900", 1)
sri.send(batch(documents, issuer.ruc, lote_key))
answers = sri.authorize_batch(lote_key)   # {access key of each voucher: Authorization}
```

A lote takes up to 50 signed vouchers and 500 KB, under an access key of its own: its series and
sequential belong to lotes only. The SRI must have authorized the issuer to send lotes.

<details>
<summary><b>Amounts and codes</b></summary>

- **Lines**: `subtotal` is the line before IVA (quantity times unit price minus discount) and `vat` its tax, both to two decimals. `price_line(code, description, quantity, unit_price, rate_code, discount)` works them out, rounding halves up. The voucher adds them up per rate.
- **IVA rate codes** (`rate_code`): the SRI's table, for example `"4"` for 15 %, `"0"` for 0 %, `"6"` not subject to IVA, `"7"` exempt.
- **Payment forms** (`Payment.form`): a `PaymentForm`, the SRI's table by its codes: `WITHOUT_FINANCIAL_SYSTEM` (`"01"`, cash), `DEBT_COMPENSATION` (`"15"`), `DEBIT_CARD` (`"16"`), `ELECTRONIC_MONEY` (`"17"`), `PREPAID_CARD` (`"18"`), `CREDIT_CARD` (`"19"`), `WITH_FINANCIAL_SYSTEM` (`"20"`, transfer) and `ENDORSED_SECURITIES` (`"21"`). Payments of the same form are added up, and a record with a code outside the table is refused.
- **Numbers**: `voucher_number(establishment, point, sequential)` writes a voucher's number as the SRI prints it, `001-002-000000015`, the same `Voucher.number` gives.
- **Credit and debit notes**: `modified=Modified(number, issued_on, reason)` names the invoice they correct; credit notes carry no payments, and a debit note's reasons are its lines.
- **IVA retention codes**: the SRI's table, `"9"` 10 %, `"10"` 20 %, `"1"` 30 %, `"11"` 50 %, `"2"` 70 %, `"3"` 100 %, `"7"` and `"8"` 0 %. Income tax and ISD codes come from the ATS catalog and bring their percentage.
- **Final consumer**: `Buyer(BuyerKind.FINAL_CONSUMER)`. The voucher writes the SRI's own name and number for it.
- **RIMPE**: an issuer in the RIMPE carries its legend, `CONTRIBUYENTE RÉGIMEN RIMPE` as an entrepreneur (`rimpe=True`) or `CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE` as a popular business (`popular_business=True`); `Taxpayer.issuer()` sets both from the registry. The SRI's published schema only admits the first legend, so the check before sending also admits the second, as its ficha técnica (2.34, annex 22) requires.

</details>

<details>
<summary><b>When something fails</b></summary>

- **`SriUnavailable`**: the SRI could not be reached or failed on its side. Nothing is lost: send the same document again later.
- **`VoucherInvalid`**: the voucher breaks the SRI's schema (the message names every reason) or the record holds no voucher. Fix the data; sending it would only be returned.
- **`CredentialRefused`**: the signature cannot sign. `problem` says why: `missing`, `unreadable` (wrong password or not a .p12), `out_of_date` or `not_for_signing` (no certificate in it carries the digitalSignature key usage).
- **Turned down**: `answer.authorized` is false and `answer.messages` carry the SRI's codes and texts, as it wrote them.

</details>

### Taxpayer registry

```python
from eqinvoice import SriRegistry

taxpayer = SriRegistry().find("1791234567001")
if taxpayer is not None and taxpayer.active:
    issuer = taxpayer.issuer(trade_name="Acme Shop")
```

- **Nobody holds it**: `find` gives `None`. A registry that cannot be reached raises `SriUnavailable`.
- **The issuer**: `taxpayer.issuer()` names the head office address the registry lists. A withholding agent or a special taxpayer passes its resolution or number (`withholding_agent=`, `special_taxpayer=`), which the registry does not publish.

### Logo

- **A mark and a name**: `MarkLogo(mark=Path("mark.svg"), name=Path("name.svg"), font=Path("Brand.ttf"))`. The mark is an SVG of rectangles and straight paths; the name is an SVG `<text>` whose runs carry their colours (a `<tspan>` for a second colour), set in the font given. They sit as a lockup: for a mark 28 high the name is 17 and the gap 8, the proportion `name_size` and `gap` change, and the name's own glyphs (ascenders and descenders included) are centred on the mark.
- **A picture**: `ImageLogo(Path("logo.png"))`, drawn at the logo's height keeping its proportions. `ImageLogo(path, one_ink_path=Path("logo-black.png"))` gives the thermal strip its own black version.
- **No logo**: leave `logo` out and the issuer's box takes the space.

On the A4 page the logo goes above the issuer's box, in colour. On the strip it goes on top, in one ink.

### Labels

`default_labels()` holds the Spanish words the SRI prints. To change them, copy `src/eqinvoice/labels/es.json`, edit the values and pass `labels=read_labels(Path("labels.json"))` to `render`. The keys stay as they are.

---

## Claude Code plugin

```
/plugin install eqinvoice --marketplace jahirxtrap/eqinvoice
```

Or add the marketplace once and install it from there:

```bash
claude plugin marketplace add jahirxtrap/eqinvoice
claude plugin install eqinvoice@eqinvoice
```

On enabling it, Claude Code asks for the book folder, the .p12 signature and its password; the password goes to the system's secure storage. The plugin starts the [MCP server](https://github.com/jahirxtrap/eqinvoice#mcp-server) with `uvx` from PyPI (it needs [uv](https://docs.astral.sh/uv/)): the first start installs it, later ones take a couple of seconds. Its tools show up as `mcp__plugin_eqinvoice_eqinvoice__*`.

## MCP server

`eqinvoice-mcp` serves one issuer over stdio. It keeps everything in a folder, the book, and signs with the issuer's .p12. Without the plugin, register it by hand:

```bash
claude mcp add --scope user eqinvoice \
  -e EQINVOICE_BOOK=/path/to/book \
  -e EQINVOICE_P12=/path/to/signature.p12 \
  -e EQINVOICE_P12_PASSWORD=... \
  -- uvx --from "eqinvoice[mcp]" eqinvoice-mcp
```

- **Issuer**: `configure_issuer` takes a RUC, the environment (`test` or `production`), the establishment and the emission point. The legal name, the address, the accounting duty, the RIMPE regime and the establishment's trade name come from the SRI's registry. A withholding agent or a special taxpayer adds its resolution number, which the registry does not publish, and the RUC of the invoicing system's provider can go on every voucher.
- **Parties**: `register_client` keeps a buyer, supplier, carrier or withheld party with its email and, for settlements, its address. A RUC is named as the registry holds it; a cédula, a passport or a foreign tax number brings its name.
- **Drafts first**: every voucher starts as a draft with the voucher it would become and a printed preview (a PDF). Nothing reaches the SRI until the draft is issued. Credit and debit notes go against an authorized invoice of the book, and a guide's recipient can name one as what it carries. Drafts kept by 1.1 still read and issue.
- **Issuing**: `issue_draft` numbers the draft, signs it, sends it and files it as the SRI leaves it: `authorized` (with its record and its printed copy), `refused` (with the SRI's messages) or `pending`, which `check_voucher` asks for again.
- **Numbering**: each environment, kind, establishment and point numbers its own series, and a number is taken for good once a voucher is signed with it.

<details>
<summary><b>Tools</b></summary>

| Tool | What it does |
|---|---|
| `look_up_taxpayer` | A RUC as the SRI's registry holds it |
| `configure_issuer` | Who issues, from which establishment and point, in which environment |
| `show_issuer` | The issuer as it is configured |
| `register_client`, `list_clients`, `forget_client` | The parties in the book |
| `draft_invoice` | An invoice for a client, with its printed preview |
| `draft_credit_note` | A credit note against an authorized invoice, whole or by lines |
| `draft_debit_note` | A debit note charging more on an authorized invoice |
| `draft_purchase_settlement` | A purchase settlement for a supplier who issues no vouchers |
| `draft_remission_guide` | A remission guide for goods on the road, to its recipients |
| `draft_withholding` | A withholding over the purchases it stands on, by income tax, IVA or ISD |
| `list_drafts`, `discard_draft` | The drafts waiting to be issued |
| `issue_draft` | The approved draft numbered, signed and sent to the SRI |
| `list_vouchers` | Every voucher numbered in the current environment |
| `check_voucher` | A pending voucher sent again and asked for |
| `summarize_sales` | A period's authorized sales per IVA rate, ready for the IVA declaration, and the vouchers still pending in it |

</details>

<details>
<summary><b>The book</b></summary>

| Path | What it holds |
|---|---|
| `profile.json` | The issuer, its establishment and point, and the environment |
| `clients/<identification>.json` | One party each |
| `drafts/<id>.json`, `drafts/<id>.pdf` | A draft and its printed preview |
| `vouchers/<environment>/<kind>/<number>/` | `signed.xml`, then `authorized.xml` and `printed.pdf`, or `refused.json` |
| `brand/` | The logo of the printed copies: `mark.svg`, `name.svg` and a `.ttf`, or `logo.png`. An optional `lockup.json` (`{"name_size": 0.76}`) sizes the name or the gap for this brand |

</details>

<details>
<summary><b>Environment variables</b></summary>

| Variable | What it does |
|---|---|
| `EQINVOICE_BOOK` | The folder the server keeps the book in |
| `EQINVOICE_P12` | The issuer's .p12 signature |
| `EQINVOICE_P12_PASSWORD` | Its password |

</details>

---

## Development

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check src tests scripts
.venv/bin/mypy
.venv/bin/pytest
```

Two scripts drive the live services:

```bash
EQINVOICE_P12=signature.p12 EQINVOICE_P12_PASSWORD=... \
  .venv/bin/python scripts/smoke_sri.py setup.json out
.venv/bin/python scripts/smoke_registry.py 1791234567001
```

- **`smoke_sri.py`**: a real round trip against the SRI's test environment. It signs a $1.00 invoice to a final consumer, sends it, waits for the authorization, reads it back and prints both layouts. `setup.json` holds `issuer` (the `Issuer` fields), `establishment`, `point` and `establishment_address`; use an emission point no other system numbers with.
- **`smoke_registry.py`**: a RUC looked up in the SRI's live registry, with its establishments and the issuer it would be.

A tag (`1.2.0`) publishes that version to PyPI and opens its release with `CHANGELOG.md`. The version lives in `pyproject.toml` and, the same, in the plugin's manifest, its marketplace entry and the package its `.mcp.json` launches; `tests/test_plugin.py` keeps them in agreement.

## License

MIT, see [LICENSE](https://github.com/jahirxtrap/eqinvoice/blob/main/LICENSE).