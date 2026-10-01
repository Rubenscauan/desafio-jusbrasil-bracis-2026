"""Detector de trechos por similaridade textual, preservando offsets Unicode."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from sklearn.feature_extraction.text import TfidfVectorizer


PROTOTYPES = {
    "jurisprudencia": (
        "acórdão precedente julgamento tribunal relator processo recurso",
        "jurisprudência súmula orientação entendimento tribunal superior",
    ),
    "lei": (
        "artigo lei dispositivo legislação código constituição",
    ),
}
WINDOW_SIZES = (4, 7, 10, 13, 16, 19)


@dataclass(frozen=True)
class SemanticCandidate:
    start: int
    end: int
    kind: str
    score: float


def fingerprint(text: str) -> str:
    """Chave resistente a caixa, acento e quebras para texto-modelo repetido."""
    decomposed = unicodedata.normalize("NFKD", text).upper()
    return " ".join(
        "".join(character for character in decomposed if not unicodedata.combining(character)).split()
    )


def _words(text: str) -> list[tuple[int, int]]:
    words: list[tuple[int, int]] = []
    start: int | None = None
    for position, character in enumerate(text):
        if character.isalnum() or character in "º°":
            if start is None:
                start = position
        elif start is not None:
            words.append((start, position))
            start = None
    if start is not None:
        words.append((start, len(text)))
    return words


def candidates(text: str, per_kind: int = 4) -> list[SemanticCandidate]:
    """Propõe janelas que se parecem com uma referência jurídica.

    A primeira etapa é TF-IDF de caracteres, portanto tolera pequenas falhas
    de OCR. Ela é deliberadamente apenas um gerador de candidatos; a resolução
    contra a base canônica continua sendo a fonte de verdade para citações reais.
    """
    words = _words(text)
    windows: list[tuple[int, int, str]] = []
    for first in range(len(words)):
        for size in WINDOW_SIZES:
            last = first + size
            if last <= len(words):
                start, end = words[first][0], words[last - 1][1]
                windows.append((start, end, text[start:end]))
    if not windows:
        return []

    prototype_texts = [item for values in PROTOTYPES.values() for item in values]
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
    vectors = vectorizer.fit_transform(prototype_texts + [item[2] for item in windows])
    prototype_vectors = vectors[: len(prototype_texts)]
    window_vectors = vectors[len(prototype_texts) :]

    offset = 0
    result: list[SemanticCandidate] = []
    for kind, prototypes in PROTOTYPES.items():
        scores = (window_vectors @ prototype_vectors[offset : offset + len(prototypes)].T).max(axis=1)
        values = scores.toarray().ravel()
        threshold = 0.24 if kind == "lei" else 0.30
        selected = sorted(range(len(windows)), key=values.__getitem__, reverse=True)
        kept = 0
        for index in selected:
            if values[index] < threshold or kept >= per_kind:
                break
            start, end, _ = windows[index]
            if any(start < saved.end and saved.start < end and saved.kind == kind for saved in result):
                continue
            result.append(SemanticCandidate(start, end, kind, float(values[index])))
            kept += 1
        offset += len(prototypes)
    return result
