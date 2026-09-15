from pathlib import Path


def _resolve_path(path: str) -> Path:
    """
    Resolve common user-facing paths.
    """

    path = path.strip()

    aliases = {
        "desktop": "~/Desktop",
        "documents": "~/Documents",
        "downloads": "~/Downloads",
        "home": "~",
    }

    normalized = path.lower()

    if normalized in aliases:
        path = aliases[normalized]

    return Path(path).expanduser().resolve()


def list_files(
    path: str = ".",
    directory: str | None = None,
) -> str:

    if directory is not None:
        path = directory

    directory_path = _resolve_path(path)

    if not directory_path.exists():
        return (
            f"Directory not found: "
            f"{directory_path}"
        )

    if not directory_path.is_dir():
        return (
            f"Not a directory: "
            f"{directory_path}"
        )

    entries = sorted(
        directory_path.iterdir(),
        key=lambda item: (
            not item.is_dir(),
            item.name.lower(),
        ),
    )

    if not entries:
        return (
            f"{directory_path} is empty."
        )

    lines = []

    for entry in entries:

        prefix = (
            "[DIR]"
            if entry.is_dir()
            else "[FILE]"
        )

        lines.append(
            f"{prefix} {entry.name}"
        )

    return "\n".join(lines)


def find_files(
    name: str,
    path: str = "~",
) -> str:

    root = _resolve_path(path)

    if not root.exists():
        return (
            f"Directory not found: "
            f"{root}"
        )

    matches = []

    for item in root.rglob("*"):

        if name.lower() in item.name.lower():
            matches.append(str(item))

        if len(matches) >= 50:
            break

    if not matches:
        return (
            f"No files or folders found "
            f"matching '{name}'."
        )

    return "\n".join(matches)


def create_folder(path: str) -> str:

    directory = _resolve_path(path)

    if directory.exists():

        if directory.is_dir():
            return (
                f"Folder already exists: "
                f"{directory}"
            )

        return (
            f"A file already exists at: "
            f"{directory}"
        )

    directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    return (
        f"Folder created: "
        f"{directory}"
    )


def read_file(path: str) -> str:

    file_path = _resolve_path(path)

    if not file_path.exists():
        return (
            f"File not found: "
            f"{file_path}"
        )

    if not file_path.is_file():
        return (
            f"Not a file: "
            f"{file_path}"
        )

    try:
        return file_path.read_text(
            encoding="utf-8"
        )

    except UnicodeDecodeError:
        return (
            "I can't read this as a UTF-8 "
            f"text file: {file_path}"
        )


def write_file(
    path: str,
    content: str,
) -> str:

    file_path = _resolve_path(path)

    file_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_path.write_text(
        content,
        encoding="utf-8",
    )

    return (
        f"File written: "
        f"{file_path}"
    )