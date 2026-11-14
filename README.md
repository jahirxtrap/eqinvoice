# ecuador-einvoicing

Electronic invoices and credit notes for Ecuador's SRI (Servicio de Rentas Internas), offline scheme version 1.1.0. It builds the voucher, signs it XAdES-BES with the issuer's .p12, sends it to the SRI, reads the authorization back and prints the RIDE on an A4 page or an 80 mm receipt strip.

It holds no database and no web framework: it takes plain values in and gives documents back, so any system can put it behind its own storage and screens.

## Capabilities

- **Vouchers**: invoices (factura) and credit notes (nota de crédito), checked against the SRI's own XSD before they leave.
- **Access key**: the 49-digit clave de acceso with its modulo 11 check digit, and the issue date on the SRI's clock.
- **Signature**: XAdES-BES with the issuer's .p12 and its password, refused with a reason when it is missing, unreadable or out of date.
- **SRI web services**: reception and authorization, in the test environment (celcer) or in production (cel).
- **Simulated SRI**: a simulated signature and an SRI that authorizes on the spot, for development and tests.
- **Printed copy (RIDE)**: an A4 page or an 80 mm strip, with the SRI's full block of totals and the access key as a barcode (page) or a QR code (strip).
- **Logo**: a PNG or JPEG picture (with an optional one-ink copy for thermal printers), or a mark and a name drawn from SVG as vectors.
- **Labels**: the printed words are data. Spanish by default, as the SRI prints them; any file of the same shape replaces them.
- **Taxpayer registry**: a RUC looked up in the SRI's public registry (legal name, standing, regime, accounting duty, withholding agent, establishments), and the `Issuer` its vouchers name.
- **Priced lines**: `price_line` works out a line's subtotal and IVA to the cent from its quantity, unit price, discount and the SRI's rate code.
- **MCP desk**: an MCP server on top of the library that keeps one issuer's clients, drafts and vouchers in a folder, so an assistant can draft, preview, issue and follow them.

## Install

The repository is private, so pip needs access to it:

```bash
pip install "ecuador-einvoicing @ git+https://github.com/jahirxtrap/ecuador-einvoicing@v1.0.0"
```

Python 3.11 or newer.

## Issue an invoice

```python
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ecuador_einvoicing import (
    Buyer,
    BuyerKind,
    Environment,
    Issuer,
    Line,
    Payment,
    SriWebServices,
    Voucher,
    VoucherKind,
    access_key,
    issue_date,
    read,
    read_credential,
    sign_voucher,
)
from ecuador_einvoicing.ride import ImageLogo, Layout, render

issuer = Issuer(
    legal_name="ACME COMERCIAL S.A.S.",
    ruc="1790011223001",
    head_office_address="AV. AMAZONAS N10-20, QUITO",
    keeps_accounts=True,
)
credential = read_credential(Path("signature.p12"), "p12 password")
now = datetime.now(UTC)
issued_on = issue_date(now)
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
    lines=(
        Line(
            code="SKU-1",
            description="Cafe molido (500 g)",
            quantity=Decimal("1"),
            unit_price=Decimal("10.00"),
            discount=Decimal("0"),
            subtotal=Decimal("10.00"),
            vat=Decimal("1.50"),
            rate_code="4",
            rate_percentage=Decimal("15.00"),
        ),
    ),
    payments=(Payment("01", Decimal("11.50")),),
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

## Amounts and codes

- **Lines come already split**: `subtotal` is the line before IVA (quantity times unit price minus discount) and `vat` its tax, both to two decimals. The voucher adds them up per rate. `price_line(code, description, quantity, unit_price, rate_code, discount)` splits one for you, rounding halves up.
- **IVA rate codes** (`rate_code`): the SRI's table, for example `"4"` for 15 %, `"0"` for 0 %, `"6"` not subject to IVA, `"7"` exempt.
- **Payment forms** (`Payment.form`): the SRI's table, for example `"01"` without the financial system (cash), `"16"` debit card, `"19"` credit card, `"20"` other with the financial system (transfer). Payments of the same form are added up.
- **Credit notes**: `modified=Modified(number, issued_on, reason)` names the invoice they correct; credit notes carry no payments.
- **Final consumer**: `Buyer(BuyerKind.FINAL_CONSUMER)`. The voucher writes the SRI's own name and number for it.

## When something fails

- **`SriUnavailable`**: the SRI could not be reached or failed on its side. Nothing is lost: send the same document again later.
- **`VoucherInvalid`**: the voucher breaks the SRI's schema (the message names every reason) or the record holds no voucher. Fix the data; sending it would only be returned.
- **`CredentialRefused`**: the signature cannot sign. `problem` says why: `missing`, `unreadable` (wrong password or not a .p12) or `out_of_date`.
- **Turned down**: `answer.authorized` is false and `answer.messages` carry the SRI's codes and texts, as it wrote them.

## Logo

- **A picture**: `ImageLogo(Path("logo.png"))`, drawn at the logo's height keeping its proportions. `ImageLogo(path, one_ink_path=Path("logo-black.png"))` gives the thermal strip its own black version.
- **A mark and a name**: `MarkLogo(mark=Path("mark.svg"), name=Path("name.svg"), font=Path("Brand-Bold.ttf"))`. The mark is an SVG of rectangles and straight paths; the name is an SVG `<text>` whose runs carry their colours (a `<tspan>` for a second colour). The name is set in the font given, beside the mark and centred on it; `name_size` and `gap` set its size and its distance as fractions of the mark.
- **No logo**: leave `logo` out and the issuer's box takes the space.

On the A4 page the logo goes above the issuer's box, in colour. On the strip it goes on top, in one ink.

## Labels

`default_labels()` holds the Spanish words the SRI prints. To change them, copy `src/ecuador_einvoicing/labels/es.json`, edit the values and pass `labels=read_labels(Path("labels.json"))` to `render`. The keys stay as they are.

## Taxpayer registry

```python
from ecuador_einvoicing import SriRegistry

taxpayer = SriRegistry().find("1791234567001")
if taxpayer is not None and taxpayer.active:
    issuer = taxpayer.issuer(trade_name="Acme Shop")
```

- **Nobody holds it**: `find` gives `None`. A registry that cannot be reached raises `SriUnavailable`.
- **The issuer**: `taxpayer.issuer()` names the head office address the registry lists. A withholding agent or a special taxpayer passes its resolution or number (`withholding_agent=`, `special_taxpayer=`), which the registry does not publish.
- **Development**: `SimulatedRegistry(taxpayer, ...)` answers only for the taxpayers it was given.

## MCP desk

`ecuador-einvoicing-mcp` is an MCP server (stdio) for one issuer. It keeps everything in a folder, the book, and signs with the issuer's .p12:

```bash
pip install "ecuador-einvoicing[mcp] @ git+https://github.com/jahirxtrap/ecuador-einvoicing"
claude mcp add --scope user einvoicing \
  -e EINVOICING_BOOK=/path/to/book \
  -e EINVOICING_P12=/path/to/signature.p12 \
  -e EINVOICING_P12_PASSWORD=... \
  -- ecuador-einvoicing-mcp
```

- **Issuer**: `configure_issuer` takes a RUC, the environment (`test` or `production`), the establishment and the emission point; the legal name, the address and the accounting duty come from the SRI's registry. `look_up_taxpayer` shows any RUC.
- **Clients**: `register_client` keeps a buyer and its email. A RUC is named as the registry holds it; a cédula or a passport brings its name.
- **Drafts**: `draft_invoice` and `draft_credit_note` (the whole invoice, or the lines given) answer with the voucher it would become and a printed preview (a PDF). Nothing reaches the SRI yet.
- **Issuing**: `issue_draft` numbers the draft, signs it, sends it and files it as the SRI leaves it: `authorized` (with its record and its printed copy), `refused` (with the SRI's messages) or `pending`. `check_voucher` asks again for a pending one.
- **Numbering**: each environment, kind, establishment and point numbers its own series, and a number is taken for good once a voucher is signed with it.

The book holds `profile.json`, `clients/`, `drafts/` and `vouchers/<environment>/<kind>/<number>/` (`signed.xml`, `authorized.xml`, `printed.pdf`, `refused.json`). A `brand/` folder gives the printed copies their logo: `mark.svg`, `name.svg` and a `.ttf` for a mark and a name, or `logo.png`.

## Development

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check src tests scripts
.venv/bin/mypy
.venv/bin/pytest
```

`scripts/smoke_sri.py` makes a real round trip against the SRI's test environment: it signs a $1.00 invoice to a final consumer, sends it, waits for the authorization, reads it back and prints both layouts. It needs the issuer's data in a JSON file and the .p12 in the environment:

```bash
EINVOICING_P12=signature.p12 EINVOICING_P12_PASSWORD=... \
  .venv/bin/python scripts/smoke_sri.py setup.json out
```

`setup.json` holds `issuer` (the `Issuer` fields), `establishment`, `point` and `establishment_address`. Use an emission point no other system numbers with, so its sequentials never collide.

`scripts/smoke_registry.py` looks a RUC up in the SRI's live registry and prints the taxpayer, its establishments and the issuer it would be:

```bash
.venv/bin/python scripts/smoke_registry.py 1791234567001
```