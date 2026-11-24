"""Render overlapping declarations once while retaining source coordinates."""
import json


def source_context(graph, required):
    covered = {graph.initializers[n] for n in required if n in graph.initializers
               and graph.nodes[n].text.startswith(graph.nodes[graph.initializers[n]].text)}
    texts = {}
    for identity in sorted(set(required) - covered):
        node = graph.nodes[identity]
        texts.setdefault(node.text, []).append((node.side, node.path, node.start, node.end))
    sources = sorted({loc[:2] for locations in texts.values() for loc in locations})
    if not sources:
        return ''
    indices = {source: i for i, source in enumerate(sources)}
    sections = ['sources (index = revision, path): '+json.dumps(sources, separators=(',', ':'))]
    for text, locations in texts.items():
        coordinates = [f'{indices[side, path]}:{start}-{end}' for side, path, start, end in sorted(set(locations))]
        sections.append('['+' '.join(coordinates)+']\n'+text)
    return '\n'.join(sections)
