import re

from scanner.config import load_companies, load_profile, load_yaml


def test_profile_valid():
    profile = load_profile()
    assert profile.experience


def test_bullet_metrics_appear_in_text():
    # Every metric must be traceable to the bullet text (basis for the fabrication check).
    for exp in load_profile().experience:
        for b in exp.bullets:
            for m in b.metrics:
                number = re.search(r"\d[\d,.]*%?", m).group()
                assert number in b.text, f"{b.id}: {m!r}"


def test_keyword_patterns_compile():
    def walk(node):
        if isinstance(node, str):
            re.compile(node, re.IGNORECASE)
        elif isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for x in node.values():
                walk(x)

    walk(load_yaml("keywords.yaml"))


def test_scoring_components_sum_to_100():
    assert sum(load_yaml("scoring.yaml")["components"].values()) == 100


def test_companies_unique():
    slugs = [(c.ats, c.slug) for c in load_companies()]
    assert len(slugs) == len(set(slugs))
