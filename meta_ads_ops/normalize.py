"""Normalização de texto usada para comparar nomes de cidade/conjunto sem
depender de acentuação ou espaçamento exato."""

import unicodedata


def normalizar(texto: object) -> str:
    texto = str(texto or "").strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()
