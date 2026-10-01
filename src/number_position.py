"""Índice genérico de identificadores numéricos e suas primeiras ocorrências."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path


NUMERIC_SEPARATORS = frozenset(". ,-/–\n\t")
SENTENCE_BOUNDARIES = frozenset(";:!?")


def numeric_anchors(text: str, minimum_digits: int = 3) -> list[tuple[int, int, str]]:
    """Extrai números formatados sem conhecer classe processual ou tribunal."""
    anchors: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(text):
        if not text[cursor].isdigit() or (cursor > 0 and text[cursor - 1].isalpha()):
            cursor += 1
            continue
        start = cursor
        while cursor < len(text) and (text[cursor].isdigit() or text[cursor] in NUMERIC_SEPARATORS):
            cursor += 1
        end = cursor
        digits = "".join(character for character in text[start:end] if character.isdigit())
        if len(digits) >= minimum_digits:
            anchors.append((start, end, digits))
    return anchors


class NumberPositionIndex:
    def __init__(self, database: Path):
        # Chave: número normalizado. Valor: documento -> primeira posição.
        self.occurrences: dict[str, dict[int, int]] = defaultdict(dict)
        with sqlite3.connect(database) as connection:
            documents = connection.execute("SELECT id, texto FROM documentos").fetchall()
        for canonical_id, text in documents:
            for start, _, number in numeric_anchors(text):
                previous = self.occurrences[number].get(canonical_id)
                if previous is None or start < previous:
                    self.occurrences[number][canonical_id] = start

    def rank(self, citation: str, limit: int = 10) -> list[tuple[int, int, str]]:
        """Retorna id, primeira posição e âncora usada, priorizando especificidade."""
        anchors = numeric_anchors(citation)
        if not anchors:
            return []
        # A âncora mais longa costuma ser o número processual; datas curtas perdem.
        _, _, number = max(anchors, key=lambda item: len(item[2]))
        candidates = self.occurrences.get(number, {})
        return [(canonical_id, position, number) for canonical_id, position in sorted(candidates.items(), key=lambda item: item[1])[:limit]]

    def unique_long_number_matches(
        self, text: str, minimum_digits: int = 7
    ) -> list[tuple[int, int, int, str]]:
        """Localiza identificadores longos que apontam para um único documento.

        O método não conhece siglas processuais: parte apenas do identificador
        numérico e da base canônica. Isso recupera grafias novas de processos
        sem transformar anos, artigos curtos ou números soltos em citações.
        """
        matches: list[tuple[int, int, int, str]] = []
        for start, end, number in numeric_anchors(text, minimum_digits):
            documents = self.occurrences.get(number, {})
            if len(documents) == 1:
                matches.append((start, end, next(iter(documents)), number))
        return matches

    def unique_marked_number_matches(
        self, text: str, minimum_digits: int = 4
    ) -> list[tuple[int, int, int, str]]:
        """Recupera números menores quando há um marcador numérico explícito.

        O sinal é estrutural (``nº``, ``no`` ou ``n.`` imediatamente antes do
        número), não uma lista de classes processuais. A exigência de resolução
        única na base continua evitando números comuns do texto corrido.
        """
        matches: list[tuple[int, int, int, str]] = []
        for start, end, number in numeric_anchors(text, minimum_digits):
            prefix = text[max(0, start - 8) : start].upper()
            compact = "".join(character for character in prefix if not character.isspace())
            if not compact.endswith(("N", "N.", "Nº", "N°", "NO")):
                continue
            documents = self.occurrences.get(number, {})
            if len(documents) == 1:
                matches.append((start, end, next(iter(documents)), number))
        return matches


def citation_envelope(
    text: str, number_start: int, number_end: int, number: str
) -> tuple[int, int]:
    """Recorta a unidade textual que contém um identificador, sem regex.

    O tamanho do identificador determina o contexto à esquerda: números de
    processo longos precisam de pouco contexto; identificadores curtos podem
    vir após uma denominação maior. Não usamos uma lista de siglas ou tribunais.
    """
    # Quanto mais longo o identificador, mais informativo ele próprio é. Um
    # recorte curto evita arrastar a oração anterior e preserva o span útil.
    left_window = max(24, 80 - int(2.5 * len(number)))
    left_limit = max(0, number_start - left_window)
    start = left_limit
    while start < number_start and not text[start].isspace():
        start += 1
    while start < number_start and text[start].isspace():
        start += 1

    # numeric_anchors aceita pontuação entre dígitos e pode consumir a vírgula
    # que inicia a oração seguinte. Terminamos no último algarismo primeiro.
    end = number_end
    while end > number_start and not text[end - 1].isdigit():
        end -= 1
    # Inclui apenas um sufixo curto formado por UF/parênteses, sem avançar para
    # palavras da oração seguinte.
    suffix_limit = min(len(text), end + 8)
    while end < suffix_limit and (text[end].isupper() or text[end] in " /-()"):
        end += 1
    while end > number_start and text[end - 1].isspace():
        end -= 1
    return start, end
