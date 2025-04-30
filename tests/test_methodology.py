import os
import re
from autogen.code_utils import MD_CODE_BLOCK_PATTERN
from pydantic import BaseModel, Field
from typing import List

os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent

class AstroPilot:
    class Input(BaseModel):
        idea: str = Field(description="The idea of the project")
        methodology: str = Field(description="The methodology of the project")
        results: str = Field(description="The results of the project")
        plot_paths: List[str] = Field(description="The plot paths of the project")

    def __init__(self, input_data: 'AstroPilot.Input' = None):
        self.input = input_data



# astro_pilot = AstroPilot()

input_data = AstroPilot.Input(idea="Your idea here", 
                              methodology="Your methodology here", 
                              results="Your results here",
                              plot_paths=['/path/to/plot1.png', '/path/to/plot2.png']) 

astro_pilot = AstroPilot(input_data=input_data) 

print(astro_pilot.input.model_dump_json(indent=4))


cmbagent = CMBAgent(
agent_llm_configs = {
                    'engineer': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "medium",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai"},
                    'researcher': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "medium",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai"},
})
   


task = r"""
Here are two datasets. These datasets contain subhalo and group-level properties from two different CAMELS simulations (A and B). The goal is to compare the distributions of these properties across both datasets.

groups_and_subhalos_A_df = pd.read_pickle('/Users/boris/CMBAgents/cmbagent/project_data/data/CAMELS/LOCAL/groups_and_subhalos_200_mcut5e9.pkl')
groups_and_subhalos_B_df = pd.read_pickle('/Users/boris/CMBAgents/cmbagent/project_data/data/CAMELS/LOCAL/groups_and_subhalos_n200_mcut5e9.pkl')

<Info on DATASET A>
output of groups_and_subhalos_A_df.describe().to_markdown():
\n
|       |      GroupSFR |   Group_R_Mean200 |   Group_M_Mean200 |   SubhaloGasMetallicity |   SubhaloMass |     SubhaloSFR |   SubhaloSpinMod |   SubhaloVmax |   SubhaloStellarPhotometrics_U |   SubhaloStellarPhotometrics_B |   SubhaloStellarPhotometrics_V |   SubhaloStellarPhotometrics_K |   SubhaloStellarPhotometrics_g |   SubhaloStellarPhotometrics_r |   SubhaloStellarPhotometrics_i |   SubhaloStellarPhotometrics_z |   SubhaloStarMetallicity |   SubhaloMassGAS |   SubhaloMassDM |   SubhaloMassSWP |   SubhaloMassBH |   SubhaloVelDisp |   SubhaloVmaxRad |\n|:------|--------------:|------------------:|------------------:|------------------------:|--------------:|---------------:|-----------------:|--------------:|-------------------------------:|-------------------------------:|-------------------------------:|-------------------------------:|-------------------------------:|-------------------------------:|-------------------------------:|-------------------------------:|-------------------------:|-----------------:|----------------:|-----------------:|----------------:|-----------------:|-----------------:|\n| count | 44288         |        44288      |      44288        |          20382          | 20382         | 20382          |      20382       |    20382      |                    20382       |                    20382       |                    20382       |                    20382       |                    20382       |                    20382       |                    20382       |                     20382      |          20382           |     20382        |     20382       |  20382           |  20382          |      20382       |     20382        |\n| mean  |     0.0422433 |           69.2228 |         10.7355   |              0.00238127 |    19.4284    |     0.0914536  |        317.282   |       77.9824 |                      -14.1589  |                      -14.2142  |                      -14.791   |                      -16.8777  |                      -14.5569  |                      -15.0336  |                      -15.2562  |                       -15.3799 |              0.00303963  |         1.66305  |        17.5501  |      0.213218    |      0.00210229 |         40.5932  |        10.0095   |\n| std   |     0.428835  |           47.0012 |        177.375    |              0.00467102 |   210.281     |     0.513121   |       1001.08    |       43.9643 |                        2.56885 |                        2.57772 |                        2.56798 |                        2.75787 |                        2.57637 |                        2.56583 |                        2.57107 |                         2.5952 |              0.00530367  |        27.8483   |       181.509   |      1.52578     |      0.0160016  |         25.2385  |        15.2058   |\n| min   |     0         |           41.5453 |          0.500062 |              0          |     0.0159726 |     0          |          1.17564 |       10.2123 |                      -23.1801  |                      -23.5313  |                      -24.3584  |                      -27.1762  |                      -23.9709  |                      -24.6873  |                      -25.0282  |                       -25.2637 |              0           |         0        |         0       |      0.000163077 |      0          |          4.59465 |         0.307768 |\n| 25%   |     0         |           46.8837 |          0.718632 |              0          |     1.22453   |     0          |         46.2172  |       54.1398 |                      -15.7662  |                      -15.8733  |                      -16.4076  |                      -18.5786  |                      -16.2088  |                      -16.6382  |                      -16.8536  |                       -16.9868 |              0           |         0        |         1.16785 |      0.00134983  |      0          |         26.9895  |         5.01202  |\n| 50%   |     0         |           55.7362 |          1.2075   |              0          |     3.07819   |     0          |        121.712   |       65.6042 |                      -13.2776  |                      -13.3486  |                      -13.9556  |                      -15.9564  |                      -13.702   |                      -14.208   |                      -14.4271  |                       -14.5336 |              0.000480832 |         0.138162 |         2.9099  |      0.00356472  |      0          |         33.8581  |         7.39513  |\n| 75%   |     0         |           73.2698 |          2.74314  |              0.00229632 |     7.48532   |     0.00824189 |        290.865   |       85.6452 |                      -12.128   |                      -12.1513  |                      -12.7236  |                      -14.6586  |                      -12.4893  |                      -12.9648  |                      -13.1885  |                       -13.2945 |              0.00314217  |         0.45165  |         6.96169 |      0.0227119   |      0.00195949 |         45.4213  |        11.2365   |\n| max   |    37.2313    |         1428.54   |      20331.6      |              0.0432014  | 16541.6       |    36.6898     |      57571       |      868.704  |                       -9.49047 |                       -9.63056 |                      -10.3286  |                      -12.3199  |                      -10.0174  |                      -10.6145  |                      -10.8462  |                       -10.9588 |              0.0397323   |      2358.29     |     14083.7     |     98.1971      |      1.46984    |        482.48    |       605.262    |
\n
This gives you the mean, std, min and max of all features. 

output of groups_and_subhalos_A_df.info():
<class 'pandas.core.frame.DataFrame'>
Index: 63818 entries, 0 to 295566
Data columns (total 23 columns):
 x   Column                        Non-Null Count  Dtype  
---  ------                        --------------  -----  
 0   GroupSFR                      43919 non-null  float32
 1   Group_R_Mean200               43919 non-null  float32
 2   Group_M_Mean200               43919 non-null  float32
 3   SubhaloGasMetallicity         19899 non-null  float32
 4   SubhaloMass                   19899 non-null  float32
 5   SubhaloSFR                    19899 non-null  float32
 6   SubhaloSpinMod                19899 non-null  float32
 7   SubhaloVmax                   19899 non-null  float32
 8   SubhaloStellarPhotometrics_U  19899 non-null  float32
 9   SubhaloStellarPhotometrics_B  19899 non-null  float32
 10  SubhaloStellarPhotometrics_V  19899 non-null  float32
 11  SubhaloStellarPhotometrics_K  19899 non-null  float32
 12  SubhaloStellarPhotometrics_g  19899 non-null  float32
 13  SubhaloStellarPhotometrics_r  19899 non-null  float32
 14  SubhaloStellarPhotometrics_i  19899 non-null  float32
 15  SubhaloStellarPhotometrics_z  19899 non-null  float32
 16  SubhaloStarMetallicity        19899 non-null  float32
 17  SubhaloMassGAS                19899 non-null  float32
 18  SubhaloMassDM                 19899 non-null  float32
 19  SubhaloMassSWP                19899 non-null  float32
 20  SubhaloMassBH                 19899 non-null  float32
 21  SubhaloVelDisp                19899 non-null  float32
 22  SubhaloVmaxRad                19899 non-null  float32
dtypes: float32(23)
memory usage: 6.1 MB
</Info on DATASET A>


<Info on DATASET B>
<class 'pandas.core.frame.DataFrame'>
Index: 64670 entries, 0 to 296683
Data columns (total 23 columns):
 x   Column                        Non-Null Count  Dtype  
---  ------                        --------------  -----  
 0   GroupSFR                      44288 non-null  float32
 1   Group_R_Mean200               44288 non-null  float32
 2   Group_M_Mean200               44288 non-null  float32
 3   SubhaloGasMetallicity         20382 non-null  float32
 4   SubhaloMass                   20382 non-null  float32
 5   SubhaloSFR                    20382 non-null  float32
 6   SubhaloSpinMod                20382 non-null  float32
 7   SubhaloVmax                   20382 non-null  float32
 8   SubhaloStellarPhotometrics_U  20382 non-null  float32
 9   SubhaloStellarPhotometrics_B  20382 non-null  float32
 10  SubhaloStellarPhotometrics_V  20382 non-null  float32
 11  SubhaloStellarPhotometrics_K  20382 non-null  float32
 12  SubhaloStellarPhotometrics_g  20382 non-null  float32
 13  SubhaloStellarPhotometrics_r  20382 non-null  float32
 14  SubhaloStellarPhotometrics_i  20382 non-null  float32
 15  SubhaloStellarPhotometrics_z  20382 non-null  float32
 16  SubhaloStarMetallicity        20382 non-null  float32
 17  SubhaloMassGAS                20382 non-null  float32
 18  SubhaloMassDM                 20382 non-null  float32
 19  SubhaloMassSWP                20382 non-null  float32
 20  SubhaloMassBH                 20382 non-null  float32
 21  SubhaloVelDisp                20382 non-null  float32
 22  SubhaloVmaxRad                20382 non-null  float32
dtypes: float32(23)
memory usage: 6.2 MB
</Info on DATASET B>

Datasets A and B are catalogs of objects (groups and subhalos) that inherently depend the local primordial non-Gaussianity parameter values fNL=200 for A and fNL=-200 for B.


Deacription of the features: 
 0   GroupSFR                      Sum of the individual star formation rates of all gas cells in this group. In units of Msun/yr.
 1   Group_R_Mean200               Comoving Radius of a sphere centered at the GroupPos of this Group whose mean density is 200 times the mean density of the Universe, at the time the halo is considered. In units of ckpc/h.
 2   Group_M_Mean200               Total Mass of this group enclosed in a sphere whose mean density is 200 times the mean density of the Universe, at the time the halo is considered, in units of 1e10 Msun/h.
 3   SubhaloGasMetallicity         Mass-weighted average metallicity (Mz/Mtot, where Z = any element above He) of the gas cells bound to this Subhalo, but restricted to cells within twice the stellar half mass radius. 
 4   SubhaloMass                   Total mass of all member particle/cells which are bound to this Subhalo, of all types. Particle/cells bound to subhaloes of this Subhalo are NOT accounted for. In units of 1e10 Msun/h.
 5   SubhaloSFR                    Sum of the individual star formation rates of all gas cells in this subhalo. In units of Msun/yr.
 6   SubhaloSpinMod                Total 3D spin modulus, computed for each as the mass weighted sum of the relative coordinate times relative velocity of all member particles/cells. In units of (kpc/h)(km/s).
 7   SubhaloVmax                   Maximum value of the spherically-averaged rotation curve. All available particle types (e.g. gas, stars, DM, and SMBHs) are included in this calculation. In units of km/s.
 8   SubhaloStellarPhotometrics_U  Magnitude in U-band based on the summed-up luminosities of all the stellar particles of the group. In mag units.
 9   SubhaloStellarPhotometrics_B  Magnitude in B-band based on the summed-up luminosities of all the stellar particles of the group. In mag units. 
 10  SubhaloStellarPhotometrics_V  Magnitude in V-band based on the summed-up luminosities of all the stellar particles of the group. In mag units. 
 11  SubhaloStellarPhotometrics_K  Magnitude in K-band based on the summed-up luminosities of all the stellar particles of the group. In mag units. 
 12  SubhaloStellarPhotometrics_g  Magnitude in g-band based on the summed-up luminosities of all the stellar particles of the group. In mag units. 
 13  SubhaloStellarPhotometrics_r  Magnitude in r-band based on the summed-up luminosities of all the stellar particles of the group. In mag units.
 14  SubhaloStellarPhotometrics_i  Magnitude in i-band based on the summed-up luminosities of all the stellar particles of the group. In mag units.
 15  SubhaloStellarPhotometrics_z  Magnitude in z-band based on the summed-up luminosities of all the stellar particles of the group. In mag units.
 16  SubhaloStarMetallicity        Mass-weighted average metallicity (Mz/Mtot, where Z = any element above He) of the star particles bound to this Subhalo, but restricted to stars within twice the stellar half mass radius.
 17  SubhaloMassGAS                Gas mass of all member particle/cells which are bound to this Subhalo, separated by type. Particle/cells bound to subhaloes of this Subhalo are NOT accounted for. In units of 1e10 Msun/h.
 18  SubhaloMassDM                 Dark Matter mass of all member particle/cells which are bound to this Subhalo, separated by type. Particle/cells bound to subhaloes of this Subhalo are NOT accounted for. In units of 1e10 Msun/h.
 19  SubhaloMassSWP                Mass of Stars & Wind particles which are bound to this Subhalo, separated by type. Particle/cells bound to subhaloes of this Subhalo are NOT accounted for. In units of 1e10 Msun/h.
 20  SubhaloMassBH                 Black Hole mass of all member particle/cells which are bound to this Subhalo, separated by type. Particle/cells bound to subhaloes of this Subhalo are NOT accounted for. In units of 1e10 Msun/h.
 21  SubhaloVelDisp                One-dimensional velocity dispersion of all the member particles/cells (the 3D dispersion divided by sqrt(3))
 22  SubhaloVmaxRad                Maximum value of the spherically-averaged rotation curve. All available particle types (e.g. gas, stars, DM, and SMBHs) are included in this calculation.

Note that Subhalo features are not available for groups (set to NaN) and group features are not available for subhalos (set to NaN).
Groups and subhalos should be considered as separate objects (they dont have the same features). Within groups and within subhalos, there are no missing values.

**Project Idea:** **Effects on Star Formation and Stellar Assembly in Different fNL Scenarios**
This idea was selected because of the following reasons:
- **Feasibility:** This project leverages the robust statistical samples available for star formation rates and stellar photometric properties. The datasets provide a solid basis for comparing GroupSFR, SubhaloSFR, and multiple photometric bands that are less affected by sparsity issues.
- **Originality:** There is significant interest in linking early-universe conditions (captured by different fNL values) with the observable processes of star formation. The idea taps into a relatively under-explored aspect of how the primordial density fluctuations might impact baryonic processes, offering fresh insights into the connection between cosmological initial conditions and galaxy evolution.
- **Scientific Impact:** Star formation is a cornerstone of galaxy evolution, and understanding its sensitivity to initial conditions can have far-reaching implications. This project could help constrain models of galaxy formation by providing measurable differences in luminosity, color, and star formation efficiency. Moreover, linking these observable properties to the theoretical framework of primordial non-Gaussianity may inform future observational strategies and cosmological parameter estimation.

"""

planner_append_instructions = r"""
Given these datasets, and information on the features and project idea, we want to design a methodology to implement this idea.
The goal of the task is to write a detailed description of the methodology that will be used to perform the project analysis.

1. **Elicit Project-Specific Reasoning:**
   - Ask the *researcher* to provide reasoning for the exploratory data analysis (EDA) tasks relevant to the given project idea.
   - Clarify the specific hypotheses, assumptions, or questions the EDA should help investigate.

2. **Conduct Exploratory Data Analysis:**
   - Collaborate with the *engineer* to perform the EDA on the provided datasets.
   - Ensure the analysis is comprehensive, covering distributions, correlations, missing data patterns, outliers, and relevant domain-specific features.

3. **Synthesize EDA Insights:**
   - Analyze the EDA results with the *researcher*.
   - Focus on understanding how the findings inform modeling choices, preprocessing needs, or feature selection.

4. **Write the Methodology description:**
   - With the *researcher*, write a **detailed description (approximately 500 words)** describing the methodology that will be used to perform the project analysis.
   - The description should clearly outline the steps, techniques, and rationale derived from the exploratory data analysis.
   - Include relevant results from the EDA in the form of key statistics or tables (no plots and no references to saved files).
   - The focus should be strictly on the methods and workflow for this specific project to be performed. **do not include** any discussion of future directions, future work, project extensions, or limitations.
   - The description should be written as if it were a senior researcher explaining to her research assistant how to perform the project.

The plan must end with the Methodology description generated by the researcher. It is in 4 steps with:
researcher->engineer->researcher->researcher
"""

engineer_append_instructions = r"""
Given these datasets, and information on the features and project idea, we want to design a methodology to implement this idea.
The goal of the task is to write a detailed description of the methodology that will be used to perform the project analysis.

Warnings: 
Some feature columns have around 40k non-null entries. Although vectorized operations (like np.percentile, np.concatenate) are efficient, they do take longer on larger arrays. 
You must make sure the code is well optimized for operations on large arrays. 
For plots involving features: 
- making sure dynamical ranges are well captured (carefully adjust the binning, and log or linear axes scales, for each feature).
For histograms (if needed):
-Use log-scale for features with values spanning several orders of magnitudes. 
-Use linear scale for Photometrics feature, but in general log-log in both x and y axes will be useful! 
-Don't include null or nan values in the histogram counts, nonetheless, although the NaN entries are useless, it might be useful to keep track of the zero counts for some features.

"""


researcher_append_instruction = r"""
Given these datasets, and information on the features and project idea, we want to design a methodology to implement this idea.
The goal of the task is to write a detailed description of the methodology that will be used to perform the project analysis.

- When asked about Elicit Project-Specific Reasoning, your goal is to clarify the specific hypotheses, assumptions, or questions the EDA should help investigate.
- When asked about Synthesize EDA Insights, your goal is to focus on understanding how the findings inform modeling choices, preprocessing needs, or feature selection.
- When asked about generating the Methodology description, your focus should be strictly on the statistical and machine learning methods for this specific project to be performed. **Do not include** any discussion of future directions, future work, project extensions, or limitations.
the methodology description should be written as if it were a senior researcher explaining to her research assistant how to perform the project. 

"""


cmbagent.solve(task,
               max_rounds=500,
               initial_agent="planner",
            #    initial_agent="researcher",
            #    mode="one_shot"
               shared_context = {'feedback_left': 1,
                                 'maximum_number_of_steps_in_plan': 4,
                                 'planner_append_instructions': planner_append_instructions,
                                 'engineer_append_instructions': engineer_append_instructions,
                                 'researcher_append_instruction': researcher_append_instruction}
              )


# template for one-shot eval
# Extract the task result from the chat history, assuming we are interested in the executor's output
try:
    for obj in cmbagent.chat_result.chat_history[::-1]:
        if obj['name'] == 'researcher_response_formatter':
            result = obj['content']
            break
    cmbagent.task_result = result
except:
    cmbagent.task_result = None

extracted_methodology = re.findall(MD_CODE_BLOCK_PATTERN, cmbagent.task_result, flags=re.DOTALL)[0]
# print(extracted_methodology)
clean_methodology = re.sub(r'^<!--.*?-->\s*\n', '', extracted_methodology)
astro_pilot.input.methodology = clean_methodology

# astro_pilot.input.methodology = re.findall(MD_CODE_BLOCK_PATTERN, cmbagent.task_result, flags=re.DOTALL)[0]

# import sys; sys.exit()

# import sys; sys.breakpointhook = pdb.set_trace
# breakpoint()  # instead of pdb.set_trace()
# import pdb; pdb.set_trace()




planner_append_instructions = rf"""

METHODLOGY

{astro_pilot.input.methodology}

Given these datasets, and information on the features and project idea and methodology, we want to perform the project analysis and generate the results, plots and insights.
The goal is to perform the in-depth research and analysis. 

The plan must end with the insights generated by the researcher, including discussion of quantitative results and plots previously generated. It is in 2 steps with:
engineer->researcher
"""


engineer_append_instructions = rf"""
METHODLOGY

{astro_pilot.input.methodology}

Given these datasets, and information on the features and project idea and methodology, we want to perform the project analysis and generate the results, plots and key statistics.
The goal is to perform the in-depth research and analysis. 

Warnings: 
Some feature columns have around 40k non-null entries. Although vectorized operations (like np.percentile, np.concatenate) are efficient, they do take longer on larger arrays. 
You must make sure the code is well optimized for operations on large arrays. 
For plots involving features: 
- making sure dynamical ranges are well captured (carefully adjust the binning, and log or linear axes scales, for each feature).
For histograms (if needed):
-Use log-scale for features with values spanning several orders of magnitudes. 
-Use linear scale for Photometrics feature, but in general log-log in both x and y axes will be useful! 
-Don't include null or nan values in the histogram counts, nonetheless, although the NaN entries are useless, it might be useful to keep track of the zero counts for some features.

"""


researcher_append_instruction =  rf"""
METHODLOGY

{astro_pilot.input.methodology}

Given the results, plots and key statistics generated by the engineer, your task is to generate a detailed discussion of the results, plots and key statistics, including reporting meaningful quantitative results, tables and references to the plots.
The goal is to perform the in-depth research and analysis. 
"""

cmbagent = CMBAgent(
agent_llm_configs = {
                    'engineer': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "high",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai"},
                    'researcher': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "high",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai"},
})
   


cmbagent.solve(task,
               max_rounds=500,
               initial_agent="planner",
            #    mode="one_shot"
               shared_context = {'feedback_left': 1,
                                 'maximum_number_of_steps_in_plan': 2,
                                 'planner_append_instructions': planner_append_instructions,
                                 'engineer_append_instructions': engineer_append_instructions,
                                 'researcher_append_instruction': researcher_append_instruction}
              )

try:
    for obj in cmbagent.chat_result.chat_history[::-1]:
        if obj['name'] == 'researcher_response_formatter':
            result = obj['content']
            break
    cmbagent.task_result = result
except:
    cmbagent.task_result = None
    
extracted_results = re.findall(MD_CODE_BLOCK_PATTERN, cmbagent.task_result, flags=re.DOTALL)[0]
# print(extracted_methodology)
clean_results = re.sub(r'^<!--.*?-->\s*\n', '', extracted_results)
astro_pilot.input.results = clean_results
astro_pilot.input.plot_paths = cmbagent.final_context['displayed_images']


print("\n\nCMBAGENT RUN DONE\n\n")
print("Methodology:")
print('--------------------------------')
print(astro_pilot.input.methodology)
print('--------------------------------')

print("\n\nResults:")
print('--------------------------------')
print(astro_pilot.input.results)
print('--------------------------------')

print("\n\nPlots:")
print('--------------------------------')
print(astro_pilot.input.plot_paths)
print('--------------------------------')



print(astro_pilot.input.model_dump_json(indent=4))
