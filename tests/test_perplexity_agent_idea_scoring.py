import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent


cmbagent = CMBAgent()


task = r"""
Here are two project ideas related to CAMELS simulation data:

 Idea 1:
    * - Non-linear Scaling Relations with Bootstrapped Regression Analysis to Unveil fNL Effects
    * instructions:
        - Hypothesis: The relationship between group-level and subhalo properties (e.g., Group_M_Mean200 vs. SubhaloMass and GroupSFR vs. SubhaloSFR) exhibits non-linear behavior modulated by the primordial non-Gaussianity (fNL) parameter.
        - Methodology: Use advanced non-linear regression techniques (e.g., polynomial regression, spline fitting, or generalized additive models) to capture non-linear scaling relations.
        - Apply bootstrapping to quantify uncertainties robustly and provide confidence intervals for the regression coefficients.
        - Introduce derived parameters, such as ratios (e.g., SubhaloMass/Group_M_Mean200), to test sensitivity to fNL variations.
        - Statistical Techniques: Perform Pearson/Spearman correlation analysis, and use robust statistical tests like the K-S test on residuals.
        - Implement regression diagnostics to check for heteroscedasticity and leverage effects.
        - Visualization: Plot multi-panel figures of the fitted scaling relations with confidence bands.
        - Create residual plots and overlay bootstrapped distributions to illustrate uncertainty estimates.
        - Implementation Plan: Develop the analysis in Python using libraries such as scikit-learn, statsmodels, NumPy, and matplotlib/seaborn.
        - Outline a code pipeline that includes data cleaning, feature engineering (e.g., creating new ratios), fitting, cross-validation, and visualization of results.
        - Expected Outcome: A quantified, non-linear mapping between key galaxy/halo properties that clearly differentiates the effects of fNL=200 versus fNL=-200, providing improved insights into structure formation.
- Idea 2:
    * - Interpretable Machine Learning for Classifying Simulation Realizations by fNL Value
    * instructions:
        - Hypothesis: A supervised machine learning model can distinguish simulation realizations by fNL using halo/galaxy properties, while interpretability techniques can reveal which features drive the classification.
        - Methodology: Preprocess the datasets with emphasis on handling missing data and normalizing features.
        - Select a set of key features (e.g., kinematic properties like SubhaloVmax, photometric magnitudes, and SFR metrics) influenced by primordial non-Gaussianity.
        - Train ensemble classifiers (Random Forest, Gradient Boosting) and twin them with cross-validation to ensure robust performance.
        - Machine Learning & Statistical Techniques: Use feature importance metrics along with modern interpretability methods such as SHAP (SHapley Additive exPlanations) or LIME to understand model decisions.
        - Perform confusion matrix analysis along with precision, recall, and ROC curve evaluations.
        - Address potential class imbalances or sampling issues with stratified sampling and data augmentation if needed.
        - Visualization: Plot feature importance rankings, SHAP value summary plots, ROC curves, and confusion matrices.
        - Create t-SNE or PCA visualizations of the feature space to inspect separability between the two fNL conditions.
        - Implementation Plan: Write a comprehensive Python script or Jupyter Notebook using scikit-learn, XGBoost, SHAP, pandas, and matplotlib/seaborn.
        - Structure the code into modules: data preprocessing, model training, evaluation, and visualization.
        - Expected Outcome: A high-accuracy classification model that not only distinguishes between fNL=200 and fNL=-200 simulations but also provides scientifically interpretable insights into which galaxy/halo properties are most affected by primordial non-Gaussianity.

Perform literature search and score these ideas based on originality and feasability. 
Based on these scores,  report on the best idea and supporting references.

Your output should be: 

- Selected idea:
- Justification: 
- Score:
- Supporting references:
    - Title, Author, Year, URL
       - Title, Author, Year, URL
       - Title, Author, Year, URL
       - Title, Author, Year, URL

"""

cmbagent.solve(task,
               max_rounds=10,
               initial_agent='control',
               mode = "one_shot",
              )