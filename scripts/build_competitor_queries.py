#!/usr/bin/env python3
"""Generate competitor-aware search prompts from configs/competitors.yaml.

This is a lightweight helper to progressively move spiru-ops from generic discovery
into competitor enrichment. For now it writes a JSON file with competitor-specific
queries that can be consumed by later pipeline steps or inspected manually.
"""

import json
import pathlib
import sys
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'configs' / 'competitors.yaml'
OUT = ROOT / 'storage' / 'state' / 'competitor_queries_preview.json'

PILLAR_QUERIES = {
    'identity': [
        '"{name}" azienda sito stabilimento coltivazione spirulina',
        '"{name}" company profile spirulina producer',
    ],
    'offering': [
        '"{name}" spirulina polvere compresse capsule estratti',
        '"{name}" product line spirulina powder tablets extract',
    ],
    'positioning': [
        '"{name}" spirulina bio premium made in Italy tracciabilità',
        '"{name}" tagline claims spirulina organic local premium',
    ],
    'channels': [
        '"{name}" e-commerce distributore farmacia erboristeria wholesale',
        '"{name}" canali vendita spirulina private label distributore',
    ],
    'pricing': [
        '"{name}" spirulina prezzo polvere compresse',
        '"{name}" wholesale private label spirulina price',
    ],
    'operations': [
        '"{name}" impianto serra coltivazione produzione spirulina',
        '"{name}" facility greenhouse production spirulina',
    ],
    'strategy': [
        '"{name}" partnership lancio funding press release spirulina',
        '"{name}" new line expansion collaboration spirulina',
    ],
    'financials': [
        '"{name}" bilancio ricavi utile dipendenti',
        '"{name}" revenue turnover employees funding',
    ],
}


def main():
    data = yaml.safe_load(REGISTRY.read_text(encoding='utf-8')) or {}
    competitors = data.get('competitors', [])
    out = []
    for comp in competitors:
        name = comp.get('canonical_name')
        if not name:
            continue
        item = {
            'canonical_name': name,
            'domain': comp.get('domain'),
            'tracking_status': comp.get('tracking_status'),
            'queries': {},
        }
        for pillar, patterns in PILLAR_QUERIES.items():
            item['queries'][pillar] = [p.format(name=name) for p in patterns]
        out.append(item)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[competitors] wrote query preview: {OUT}')


if __name__ == '__main__':
    main()
