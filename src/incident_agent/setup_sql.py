"""Fill in and split the SQL setup files."""
import re
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def render(filename: str, values: dict[str, str]) -> list[str]:
    """Replace ${name} placeholders and split into statements.

    An unknown placeholder is an error.
    """
    text = (SQL_DIR / filename).read_text()
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    for key, value in values.items():
        if not re.fullmatch(r"[\w.-]+", value):
            raise ValueError(f"unsafe value for {key}: {value!r}")
        text = text.replace("${" + key + "}", value)
    left = re.findall(r"\$\{(\w+)\}", text)
    if left:
        raise ValueError(f"missing values for: {', '.join(sorted(set(left)))}")
    return [s.strip() for s in text.split(";") if s.strip()]
