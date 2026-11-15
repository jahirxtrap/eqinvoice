from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from lxml import etree

from eqinvoice.model import BuyerKind, Environment, VoucherKind

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
SCHEMAS = {
    VoucherKind.INVOICE: "factura_V1.1.0.xsd",
    VoucherKind.CREDIT_NOTE: "NotaCredito_V1.1.0.xsd",
    VoucherKind.DEBIT_NOTE: "NotaDebito_V1.0.0.xsd",
    VoucherKind.PURCHASE_SETTLEMENT: "LiquidacionCompra_V1.1.0.xsd",
    VoucherKind.REMISSION_GUIDE: "GuiaRemision_V1.1.0.xsd",
    VoucherKind.WITHHOLDING: "ComprobanteRetencion_V2.0.0.xsd",
}
VERSIONS = {kind: name.rsplit("_V", 1)[1].removesuffix(".xsd") for kind, name in SCHEMAS.items()}
HOSTS = {
    Environment.TEST: "https://celcer.sri.gob.ec",
    Environment.PRODUCTION: "https://cel.sri.gob.ec",
}
RECEPTION_PATH = "/comprobantes-electronicos-ws/RecepcionComprobantesOffline"
AUTHORIZATION_PATH = "/comprobantes-electronicos-ws/AutorizacionComprobantesOffline"
RECEPTION_NS = "http://ec.gob.sri.ws.recepcion"
AUTHORIZATION_NS = "http://ec.gob.sri.ws.autorizacion"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
XSD_NS = "http://www.w3.org/2001/XMLSchema"

ENVIRONMENT_CODES = {Environment.TEST: "1", Environment.PRODUCTION: "2"}
ENVIRONMENT_NAMES = {Environment.TEST: "PRUEBAS", Environment.PRODUCTION: "PRODUCCIÓN"}
NORMAL_EMISSION = "1"
DOCUMENT_CODES = {
    VoucherKind.INVOICE: "01",
    VoucherKind.PURCHASE_SETTLEMENT: "03",
    VoucherKind.CREDIT_NOTE: "04",
    VoucherKind.DEBIT_NOTE: "05",
    VoucherKind.REMISSION_GUIDE: "06",
    VoucherKind.WITHHOLDING: "07",
}
ROOTS = {
    VoucherKind.INVOICE: "factura",
    VoucherKind.PURCHASE_SETTLEMENT: "liquidacionCompra",
    VoucherKind.CREDIT_NOTE: "notaCredito",
    VoucherKind.DEBIT_NOTE: "notaDebito",
    VoucherKind.REMISSION_GUIDE: "guiaRemision",
    VoucherKind.WITHHOLDING: "comprobanteRetencion",
}
INFO = {
    VoucherKind.INVOICE: "infoFactura",
    VoucherKind.PURCHASE_SETTLEMENT: "infoLiquidacionCompra",
    VoucherKind.CREDIT_NOTE: "infoNotaCredito",
    VoucherKind.DEBIT_NOTE: "infoNotaDebito",
    VoucherKind.REMISSION_GUIDE: "infoGuiaRemision",
    VoucherKind.WITHHOLDING: "infoCompRetencion",
}
CODE_TAGS = {
    VoucherKind.INVOICE: "codigoPrincipal",
    VoucherKind.PURCHASE_SETTLEMENT: "codigoPrincipal",
    VoucherKind.CREDIT_NOTE: "codigoInterno",
}
PARTIES = {
    VoucherKind.INVOICE: "Comprador",
    VoucherKind.PURCHASE_SETTLEMENT: "Proveedor",
    VoucherKind.CREDIT_NOTE: "Comprador",
    VoucherKind.DEBIT_NOTE: "Comprador",
    VoucherKind.WITHHOLDING: "SujetoRetenido",
}
ADDRESSED = (VoucherKind.INVOICE, VoucherKind.PURCHASE_SETTLEMENT)
PRICED = (*CODE_TAGS, VoucherKind.DEBIT_NOTE)
NOTES = (VoucherKind.CREDIT_NOTE, VoucherKind.DEBIT_NOTE)
TOTAL_TAGS = {
    VoucherKind.INVOICE: "importeTotal",
    VoucherKind.PURCHASE_SETTLEMENT: "importeTotal",
    VoucherKind.CREDIT_NOTE: "valorModificacion",
    VoucherKind.DEBIT_NOTE: "valorTotal",
}
TAX_TOTALS = {
    VoucherKind.INVOICE: "totalConImpuestos/totalImpuesto",
    VoucherKind.PURCHASE_SETTLEMENT: "totalConImpuestos/totalImpuesto",
    VoucherKind.CREDIT_NOTE: "totalConImpuestos/totalImpuesto",
    VoucherKind.DEBIT_NOTE: "impuestos/impuesto",
}
VOUCHER_ID = "comprobante"

BUYER_CODES = {
    BuyerKind.RUC: "04",
    BuyerKind.CEDULA: "05",
    BuyerKind.PASSPORT: "06",
    BuyerKind.FINAL_CONSUMER: "07",
    BuyerKind.FOREIGN: "08",
}
VAT_RETENTION_RATES = {
    "9": Decimal("10"),
    "10": Decimal("20"),
    "1": Decimal("30"),
    "11": Decimal("50"),
    "2": Decimal("70"),
    "3": Decimal("100"),
    "7": Decimal("0"),
    "8": Decimal("0"),
}
LOCAL_PAYMENT, ABROAD_PAYMENT = "01", "02"
FISCAL_PERIOD_FORMAT = "%m/%Y"
MOST_ITEM_DETAILS = 3
BATCH_ROOT = "lote"
BATCH_VERSION = "1.0.0"
MOST_BATCH_VOUCHERS = 50
MOST_BATCH_BYTES = 500 * 1024
FINAL_CONSUMER_ID = "9999999999999"
FINAL_CONSUMER_NAME = "CONSUMIDOR FINAL"
VAT_TAX = "2"
VAT_RATES = {
    "0": Decimal("0"),
    "2": Decimal("12"),
    "3": Decimal("14"),
    "4": Decimal("15"),
    "5": Decimal("5"),
    "6": Decimal("0"),
    "7": Decimal("0"),
    "8": Decimal("8"),
    "10": Decimal("13"),
}
ICE_TAX = "3"
IRBPNR_TAX = "5"
CURRENCY_NAME = "DOLAR"
RIMPE_TYPE = "contribuyenteRimpe"
EMAIL_FIELD = "Email"
SYSTEM_PROVIDER_FIELD = "RUC Proveedor"
YES, NO = "SI", "NO"
MOST_EXTRA_FIELDS = 15

CLOCK = ZoneInfo("America/Guayaquil")
DATE_FORMAT = "%d/%m/%Y"

RECEIVED = "RECIBIDA"
RETURNED = "DEVUELTA"
AUTHORIZED = "AUTORIZADO"
NOT_AUTHORIZED = "NO AUTORIZADO"
ALREADY_REGISTERED = "43"
STILL_PROCESSING = "70"

REGISTRY_HOST = "https://srienlinea.sri.gob.ec"
TAXPAYER_PATH = (
    "/sri-catastro-sujeto-servicio-internet/rest/ConsolidadoContribuyente/obtenerPorNumerosRuc"
)
ESTABLISHMENTS_PATH = (
    "/sri-catastro-sujeto-servicio-internet/rest/Establecimiento/consultarPorNumeroRuc"
)
REGISTRY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "application/json",
}
ACTIVE_TAXPAYER = "ACTIVO"
OPEN_ESTABLISHMENT = "ABIERTO"
RIMPE_REGIME = "RIMPE"
POPULAR_BUSINESS = "NEGOCIO POPULAR"
POPULAR_BUSINESS_LEGEND = "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"

PARSER = etree.XMLParser(
    no_network=True, resolve_entities=False, load_dtd=False, remove_blank_text=False
)


def text_at(element: etree._Element | None, path: str) -> str:
    if element is None:
        return ""
    found = element.find(path)
    return (found.text or "").strip() if found is not None else ""


def buyer_shown(kind: BuyerKind, name: str | None, identification: str | None) -> tuple[str, str]:
    named = kind != BuyerKind.FINAL_CONSUMER
    return (
        name if named and name else FINAL_CONSUMER_NAME,
        identification if named and identification else FINAL_CONSUMER_ID,
    )
