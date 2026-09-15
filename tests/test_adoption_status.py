from venue_broker.adoption_status import render_status_table, replace_status_table


def test_status_table_uses_served_metrics_as_observed_migration_proof() -> None:
    registry = {
        "callers": [
            {
                "name": "signal-collector",
                "project": "Signal Collector",
                "class": "trading",
                "weight": 8,
            },
            {
                "name": "execution-lag",
                "project": "Execution Bot",
                "class": "bulk",
                "weight": 1,
            },
        ]
    }
    metrics = {
        "instance_id": "broker-one",
        "venues": {
            "pmus": {
                "callers": {
                    "signal-collector": {"requests_served": 91},
                    "execution-lag": {"requests_served": 34},
                }
            },
            "kalshi": {
                "callers": {
                    "signal-collector": {"requests_served": 2542},
                    "other": {"requests_served": 13},
                }
            },
        },
    }

    table = render_status_table(metrics, registry)

    assert "| **Signal Collector** | ✅ observed (91) | ✅ observed (2,542) |" in table
    assert "| **Execution Bot** | ✅ observed (34) | ◻ not observed |" in table
    assert "Unconfigured caller names" in table
    assert "13" in table
    assert "direct" not in table.lower()


def test_status_replacement_changes_only_the_generated_block() -> None:
    original = (
        "# Adoption\n\nBefore prose.\n\n"
        "<!-- BEGIN GENERATED ADOPTION STATUS -->\n"
        "old table\n"
        "<!-- END GENERATED ADOPTION STATUS -->\n\n"
        "After prose.\n"
    )

    updated = replace_status_table(original, "| new | table |")

    assert updated == (
        "# Adoption\n\nBefore prose.\n\n"
        "<!-- BEGIN GENERATED ADOPTION STATUS -->\n"
        "| new | table |\n"
        "<!-- END GENERATED ADOPTION STATUS -->\n\n"
        "After prose.\n"
    )
