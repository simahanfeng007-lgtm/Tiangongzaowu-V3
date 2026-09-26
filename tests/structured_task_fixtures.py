"""Explicit typed requirements for evidence tests; no natural-language parser.

These tests exercise existing registered plans, including historical plans.
The product no longer manufactures such requirements from the user's prose.
"""
from v3 import execution_integrity as integrity


def registered_contract(obligations):
    contract = integrity.initialize_task_contract('')
    contract['desired_facts'] = [integrity._goal_fact_from_obligation(item, index)
                                 for index, item in enumerate(obligations, 1)]
    return contract
