from __future__ import annotations

import json
from pathlib import Path


class DpsCounter:
    """Contador sequencial persistente de nDPS por (CNPJ, série, ambiente).

    Só é incrementado após emissão bem-sucedida (HTTP 201). Em rejeição da Sefin
    o número fica disponível para retentativa após correção. Ambientes (producao
    vs producaorestrita) têm contadores separados — a Sefin Nacional emite com
    numeração independente por ambiente.
    """

    def __init__(self, cnpj: str, serie: str, ambiente: str, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.path = output_dir / f".dps-counter-{cnpj}-{serie}-{ambiente}.json"

    def peek(self) -> int:
        if not self.path.exists():
            return 1
        return int(json.loads(self.path.read_text())["next"])

    def consume(self, used: int) -> None:
        self.path.write_text(json.dumps({"next": used + 1}))
