"""Decode the client's ``.pbxml`` packs back into plain XML.

The format is two nested containers, both Nebula3-style FourCCs stored
little-endian (so they read reversed in a hex dump):

``KCAP`` -- "PACK", the outer archive::

    char[4]  'KCAP'
    uint32   version (1)
    repeated:
        uint16   name length
        char[n]  entry name (e.g. "a0001-start-tutorial-01-d")
        uint32   payload length
        byte[n]  payload -- a BXML blob

``LMXB`` -- "BXML", one binary XML document per entry::

    char[4]  'LMXB'
    uint32   numAttrs
    uint32   numNodes
    uint32   numStrings
    uint32[numAttrs * 2]   attribute (nameIndex, valueIndex) pairs, interleaved
    uint32[numNodes * 6]   nodes: name, firstChild, nextSibling, parent,
                           firstAttr, numAttrs
    char[]   string table: numStrings NUL-terminated UTF-8 strings

``0x7FFFFFFF`` is the nil link. Nodes carry attributes only -- the format has
no representation for element text, so no text content is lost.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path
from xml.etree import ElementTree as ET

PACK_MAGIC = b"KCAP"
BXML_MAGIC = b"LMXB"
NIL = 0x7FFFFFFF


class FormatError(RuntimeError):
    """The bytes on disk do not match the documented layout."""


def iter_pack(data: bytes):
    """Yield (entry_name, payload) for every entry in a PACK archive."""
    if data[:4] != PACK_MAGIC:
        raise FormatError(f"expected {PACK_MAGIC!r} magic, got {data[:4]!r}")
    (version,) = struct.unpack_from("<I", data, 4)
    if version != 1:
        raise FormatError(f"unsupported pack version {version}")

    offset = 8
    total = len(data)
    while offset < total:
        (name_length,) = struct.unpack_from("<H", data, offset)
        offset += 2
        name = data[offset:offset + name_length].decode("utf-8", "replace")
        offset += name_length
        (payload_length,) = struct.unpack_from("<I", data, offset)
        offset += 4
        payload = data[offset:offset + payload_length]
        if len(payload) != payload_length:
            raise FormatError(f"entry {name!r} truncated")
        offset += payload_length
        yield name, payload


def decode_bxml(blob: bytes, label: str = "<blob>") -> ET.Element:
    """Rebuild an ElementTree element from one BXML document."""
    if blob[:4] != BXML_MAGIC:
        raise FormatError(f"{label}: expected {BXML_MAGIC!r}, got {blob[:4]!r}")
    num_attrs, num_nodes, num_strings = struct.unpack_from("<III", blob, 4)

    attrs_at = 16
    nodes_at = attrs_at + 8 * num_attrs
    strings_at = nodes_at + 24 * num_nodes
    if strings_at > len(blob):
        raise FormatError(f"{label}: header overruns payload")

    raw = blob[strings_at:].split(b"\x00")
    if raw and raw[-1] == b"":
        raw.pop()
    if len(raw) != num_strings:
        raise FormatError(
            f"{label}: string table has {len(raw)} entries, header says {num_strings}"
        )
    strings = [item.decode("utf-8", "replace") for item in raw]

    pairs = struct.unpack_from(f"<{num_attrs * 2}I", blob, attrs_at)
    nodes = [
        struct.unpack_from("<6I", blob, nodes_at + 24 * index)
        for index in range(num_nodes)
    ]
    if not nodes:
        raise FormatError(f"{label}: document has no nodes")

    def build(index: int) -> ET.Element:
        name_index, first_child, _next, _parent, first_attr, attr_count = nodes[index]
        element = ET.Element(strings[name_index])
        if attr_count:
            for slot in range(first_attr, first_attr + attr_count):
                key = strings[pairs[slot * 2]]
                value = strings[pairs[slot * 2 + 1]]
                element.set(key, value)
        child = first_child
        while child != NIL:
            element.append(build(child))
            child = nodes[child][2]
        return element

    return build(0)


def merge_documents(documents: list[tuple[str, ET.Element]]) -> ET.Element:
    """Fold per-entry documents into one tree.

    Every quest entry is its own ``<Quests>`` document holding a single
    ``<Quest>``. Collapsing them into one ``<Quests>`` root keeps the result
    valid against the same shape rather than inventing a wrapper element.
    """
    roots = {element.tag for _, element in documents}
    combined = ET.Element(roots.pop() if len(roots) == 1 else "Pack")

    for name, element in documents:
        children = list(element)
        # A document normally holds one child, which is contributed directly.
        # Only record the pack entry name when it is not already the child's id,
        # so the common case stays byte-for-byte faithful to the source.
        for child in children or [element]:
            if child.get("id") != name:
                child.set("packEntry", name)
            combined.append(child)
    return combined


def convert(
    source: Path, output: Path
) -> tuple[list[tuple[str, ET.Element]], int, list[tuple[str, str]]]:
    """Decode the pack ``source`` into one XML file at ``output``.

    Returns the decoded (name, document) pairs, the number of entries, and the
    (name, error) of every entry that failed.
    """
    entries = list(iter_pack(source.read_bytes()))
    documents: list[tuple[str, ET.Element]] = []
    failures: list[tuple[str, str]] = []
    for name, payload in entries:
        try:
            documents.append((name, decode_bxml(payload, name)))
        except FormatError as error:
            failures.append((name, str(error)))

    tree = ET.ElementTree(merge_documents(documents))
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return documents, len(entries), failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        default=Path("dso-client/export_win32/quests/quests.pbxml"),
        help="the pack to decode (default: the quests of the client in dso-client)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="combined XML file to write (default: <source>.xml)",
    )
    parser.add_argument(
        "--split",
        type=Path,
        metavar="DIR",
        help="also write one XML file per pack entry into DIR",
    )
    parser.add_argument(
        "--list", action="store_true", help="only list entry names and exit"
    )
    args = parser.parse_args()

    if args.list:
        entries = list(iter_pack(args.source.read_bytes()))
        for name, payload in entries:
            print(f"{name}\t{len(payload)}")
        print(f"\n{len(entries)} entries")
        return

    output = args.output or args.source.with_suffix(".xml")
    documents, total, failures = convert(args.source, output)

    print(f"decoded {len(documents)}/{total} entries")
    print(f"wrote {output} ({output.stat().st_size:,} bytes)")

    if args.split:
        args.split.mkdir(parents=True, exist_ok=True)
        for name, element in documents:
            safe = name.replace("/", "_").replace("\\", "_")
            subtree = ET.ElementTree(element)
            ET.indent(subtree, space="  ")
            subtree.write(
                args.split / f"{safe}.xml", encoding="utf-8", xml_declaration=True
            )
        print(f"wrote {len(documents)} files into {args.split}")

    if failures:
        print(f"\n{len(failures)} entries failed:")
        for name, error in failures[:20]:
            print(f"  {name}: {error}")


if __name__ == "__main__":
    main()
