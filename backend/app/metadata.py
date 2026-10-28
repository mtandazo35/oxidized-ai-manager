import re


IDENTITY_EXPORT_PATTERN = re.compile(
    r"/system identity\s*(?:\r?\n)?set name="
    r"(?:\"(?P<quoted>[^\"\r\n]+)\"|(?P<plain>\S+))"
)

COMMENT_KEYS = {
    "version": "ros_version",
    "board-name": "board",
    "name": "identity",
}


def parse_routeros_metadata(config_text: str) -> dict[str, str]:
    """Extract identity, RouterOS version and board from an Oxidized export."""
    meta: dict[str, str] = {}
    for line in config_text.splitlines()[:80]:
        if not line.startswith("#") or ":" not in line:
            continue
        key, _, value = line[1:].partition(":")
        field = COMMENT_KEYS.get(key.strip())
        if field and value.strip() and field not in meta:
            meta[field] = value.strip()[:128]
    match = IDENTITY_EXPORT_PATTERN.search(config_text)
    if match:
        meta["identity"] = (match.group("quoted") or match.group("plain"))[:128]
    elif "identity" not in meta and (
        "ros_version" in meta or "board" in meta or "RouterOS" in config_text
    ):
        # `/export` omite los valores por defecto, así que un RouterOS sin la
        # sección `/system identity` conserva el nombre de fábrica "MikroTik".
        # Sin esto el panel mostraría "—" y parecería que no se reconoció.
        meta["identity"] = "MikroTik"
    return meta
