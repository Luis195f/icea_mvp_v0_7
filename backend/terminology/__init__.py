"""Terminology helpers for ICEA+.

v0.5.1 introduces a lightweight semantic-bridging layer to map nursing taxonomies
(NANDA/NIC/NOC) into interoperable ontologies (SNOMED CT / LOINC).

Design goals:
- Keep it optional: if no mapping is configured, the pipeline behaves as before.
- Avoid shipping licensed taxonomy content: mappings are loaded from a hospital/local file.
"""
