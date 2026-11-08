import re


def new_fs():
    return {"root": {"type": "dir", "children": {}}}


def split_path(path):
    parts = [p for p in path.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise ValueError(f"invalid path: {path!r}")
    return parts


def normalize(path):
    return "/" + "/".join(split_path(path))


def get_node(fs, path):
    node = fs["root"]
    for part in split_path(path):
        if node.get("type") != "dir" or part not in node["children"]:
            return None
        node = node["children"][part]
    return node


def add_file(fs, path, file_node):
    parts = split_path(path)
    if not parts:
        raise ValueError("a file needs a name")
    node = fs["root"]
    for part in parts[:-1]:
        node = node["children"].setdefault(part, {"type": "dir", "children": {}})
        if node.get("type") != "dir":
            raise ValueError(f"'{part}' is a file, not a folder")
    node["children"][parts[-1]] = file_node


def remove_node(fs, path):
    parts = split_path(path)
    if not parts:
        return None
    parent = get_node(fs, "/" + "/".join(parts[:-1]))
    if parent is None or parent.get("type") != "dir":
        return None
    return parent["children"].pop(parts[-1], None)


def iter_files(node, path=""):
    for name, child in node["children"].items():
        child_path = f"{path}/{name}"
        if child.get("type") == "file":
            yield child_path, child
        else:
            yield from iter_files(child, child_path)


def outline(node):
    if node.get("type") == "file":
        return {"type": "file", "size": node.get("size", 0), "shards": [chunk[1] for chunk in node.get("chunks", [])]}
    return {"type": "dir", "children": {name: outline(child) for name, child in node["children"].items()}}


def tree_lines(fs):
    lines = ["root"]

    def walk(node, prefix):
        children = list(node["children"].items())
        for i, (name, child) in enumerate(children):
            last = i == len(children) - 1
            branch = "└── " if last else "├── "
            if child.get("type") == "file":
                tag = " [encrypted]" if child.get("encrypted", True) else " [raw]"
                lines.append(prefix + branch + name + tag)
            else:
                lines.append(prefix + branch + name)
                walk(child, prefix + ("    " if last else "│   "))

    walk(fs["root"], "")
    return lines


def search(fs, query="", exact=False, use_regex=False, filter_type=None, filter_mode=None):
    pattern = re.compile(query, re.IGNORECASE) if use_regex and query else None
    query_lower = query.lower()
    results = []

    def walk(node, current_path):
        for name, child in node["children"].items():
            node_path = f"{current_path}/{name}"
            kind = child.get("type", "file")
            encrypted = child.get("encrypted", True) if kind == "file" else None

            if not query:
                match = True
            elif pattern:
                match = bool(pattern.search(name) or pattern.search(node_path))
            elif exact:
                match = name.lower() == query_lower
            else:
                match = query_lower in node_path.lower()

            if filter_type and kind != filter_type:
                match = False
            if filter_mode == "encrypted" and encrypted is not True:
                match = False
            if filter_mode == "raw" and encrypted is not False:
                match = False

            if match:
                results.append({
                    "path": node_path,
                    "name": name,
                    "type": kind,
                    "size": child.get("size", 0),
                    "encrypted": encrypted,
                    "chunks": child.get("chunks", []) if kind == "file" else [],
                })
            if kind == "dir":
                walk(child, node_path)

    walk(fs["root"], "")
    return results
