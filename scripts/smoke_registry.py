from __future__ import annotations

import sys

from ecuador_einvoicing import SriRegistry

USAGE = "usage: smoke_registry.py <ruc>"
NOT_FOUND = 1
WRONG_USE = 2


def main() -> int:
    if len(sys.argv) != 2:
        print(USAGE)
        return WRONG_USE
    taxpayer = SriRegistry().find(sys.argv[1])
    if taxpayer is None:
        print(f"nobody holds {sys.argv[1]}")
        return NOT_FOUND
    print(f"{taxpayer.ruc} {taxpayer.legal_name}: active={taxpayer.active}")
    print(f"  regime={taxpayer.regime} keeps accounts={taxpayer.keeps_accounts}")
    print(f"  agent={taxpayer.withholding_agent} special={taxpayer.special_taxpayer}")
    for held in taxpayer.establishments:
        print(f"  {held.number} head={held.head_office} open={held.open} {held.address}")
    print(f"  issues as: {taxpayer.issuer()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
