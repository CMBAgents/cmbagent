import cmbagent

task = r"""
Multi-agent systems (MAS) in artificial intelligence (AI) and computer science utilizing multiple Large Language Model agents with Retrieval Augmented Generation and that can execute code locally may become beneficial in cosmological data analysis. Especially for galaxy clusters.
"""

task = r"""
Mapping Interfacial Water States on Functionalized Graphene: A Machine
Learning-Augmented Approach to Uncover Design Principles for Tunable Water
Transport

Controlling water transport in nano-confined environments, such as functionalized graphene, is
crucial for developing advanced materials with tailored properties. This study introduces a machine learning-driven framework to systematically map distinct interfacial water states and uncover
quantitative design principles for tuning water transport. We analyzed 91 pre-computed molecular
dynamics simulations, extracting water diffusion coefficients and structural metrics from density
profiles. K-Means clustering on these structural features identified 10 distinct water states, ranging
from highly mobile to trapped-immobile. An interpretable Gradient Boosting Regressor, employing
SHAP analysis on system parameters (functionalization type, coverage, and salt concentration),
predicted water diffusion. Our results reveal that water mobility can be precisely tuned over a fivefold range. Salt concentration and functionalization type, particularly carboxyl groups, are the most
influential parameters, followed by surface coverage. Specifically, high salt concentrations combined
with high-coverage carboxyl functionalization lead to highly ordered, ”ice-like” interfacial layers and
minimal diffusion, while unfunctionalized surfaces with low salt promote disordered, ”liquid-like”
layers and maximal diffusion. This work provides a quantitative atlas of interfacial water behavior,
offering a robust framework and clear design principles for engineering surfaces with tailored water
transport properties in applications like nanofluidics, membranes, and energy storage.
"""

keywords = cmbagent.get_keywords(task, n_keywords=2, kw_type='aaai')

print(keywords)
