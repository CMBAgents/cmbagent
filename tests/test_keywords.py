import cmbagent

task = r"""
Multi-agent systems (MAS) utilizing multiple Large Language Model agents with Retrieval Augmented Generation and that can execute code locally may become beneficial in cosmological data analysis.
"""

keywords = cmbagent.get_keywords(task, n_keywords=8)

print(keywords)
