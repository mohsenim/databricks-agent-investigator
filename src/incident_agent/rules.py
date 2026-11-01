"""Fixed patterns that map error text to a failure class.

Each rule gives direct-error-match evidence. The model may add hypotheses, but these
rules make the evidence useful even without a model.
"""
import re

from .taxonomy import FailureClass

C = FailureClass

# (pattern, class, label). The order does not matter: every matching rule applies.
ERROR_RULES: list[tuple[re.Pattern[str], FailureClass, str]] = [
    (re.compile(r"UNRESOLVED_COLUMN|cannot resolve ['`\"]?[\w.]+['`\"]? given input columns|"
                r"Column ['`\"]?[\w.]+['`\"]? does not exist|"
                r"A column.*cannot be found|FIELD_NOT_FOUND", re.IGNORECASE),
     C.SCHEMA_CHANGE, "a column the code reads is missing"),
    (re.compile(r"DELTA_SCHEMA_CHANGED|DELTA_FAILED_TO_MERGE_FIELDS|DELTA_MERGE_INCOMPATIBLE|"
                r"CAST_INVALID_INPUT|cannot be cast to|DATATYPE_MISMATCH|"
                r"NUMERIC_VALUE_OUT_OF_RANGE", re.IGNORECASE),
     C.SCHEMA_CHANGE, "a column type does not match what the code expects"),
    (re.compile(r"PERMISSION_DENIED|INSUFFICIENT_PERMISSIONS|does not have (?:SELECT|USE|MODIFY)|"
                r"Permission denied|User does not have", re.IGNORECASE),
     C.PERMISSION, "access to an object was denied"),
    (re.compile(r"OutOfMemoryError|java\.lang\.OutOfMemory|Container killed|"
                r"Driver is up but is not responsive|SparkOutOfMemory|MemoryError", re.IGNORECASE),
     C.RESOURCE_LIMIT, "the process ran out of memory"),
    (re.compile(r"Library installation (?:failed|attempted)|No matching distribution|"
                r"Could not find a version that satisfies|pip.*(?:error|failed)", re.IGNORECASE),
     C.LIBRARY, "a library could not be installed"),
    (re.compile(r"\b(NameError|SyntaxError|IndentationError|TypeError|AttributeError|KeyError|"
                r"ValueError|ZeroDivisionError|ImportError|ModuleNotFoundError)\b"),
     C.CODE_OR_CONFIG, "the code raised a programming error"),
    (re.compile(r"TABLE_OR_VIEW_NOT_FOUND|PATH_NOT_FOUND|Path does not exist", re.IGNORECASE),
     C.CODE_OR_CONFIG, "a table or path the code uses was not found"),
    (re.compile(r"DELTA_TABLE_NOT_FOUND|is not a Delta table", re.IGNORECASE),
     C.UPSTREAM_JOB, "an expected input table does not exist"),
]


def classify_error(text: str) -> list[tuple[FailureClass, str]]:
    seen: set[FailureClass] = set()
    out = []
    for pattern, cls, label in ERROR_RULES:
        if pattern.search(text or "") and cls not in seen:
            seen.add(cls)
            out.append((cls, label))
    return out


_MISSING_COL = re.compile(
    r"(?:UNRESOLVED_COLUMN[^`'\"]*|cannot resolve |Column |column with name )"
    r"['`\"]([\w.]+)['`\"]", re.IGNORECASE)


def mentioned_columns(text: str) -> set[str]:
    """Column names mentioned in a missing-column error."""
    return {m.split(".")[-1] for m in _MISSING_COL.findall(text or "")}
