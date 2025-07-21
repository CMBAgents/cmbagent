import os
import base64
import tempfile
import matplotlib.pyplot as plt
from typing import Tuple, Literal
from classy import Class
from math import pi

# Valid plot and code injections
InjectionCode = Literal["template", "exact"]
InjectionPlot = Literal[
    "wrong_scalar_amplitude", 
    "truncated_multipole_range",
    "wrong_scalar_spectral_index",
    "wrong_hubble_constant",
    "no_rescaling",
    "incorrect_units",
    "wrong_cmb_spectra",
    "wrong_axis_scaling",
    "overplotting_spectra",
    "wrong_optical_depth",
    ]

injection_config: dict[str, str] | bool = False  # Disabled by default


def _save_plot_to_files() -> str:
    """Save injected plot to temp file and debug location. All plots named the same."""
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
        plt.savefig(tmp_file.name, dpi=300, bbox_inches='tight')
        
        # Also save a copy to synthetic_output
        try:
            synthetic_dir = "/Users/kahaan/Downloads/cmbagent/synthetic_output"  # TODO: make this a relative path
            os.makedirs(synthetic_dir, exist_ok=True)
            debug_path = os.path.join(synthetic_dir, "injected_plot.png")
            plt.savefig(debug_path, dpi=300, bbox_inches='tight')
            print(f"DEBUG: Injected plot saved to {debug_path} for reference")
        except Exception as e:
            print(f"DEBUG: Could not save injected plot to synthetic_output: {e}")
        
        plt.close()
        
        # Read and encode as base64
        with open(tmp_file.name, 'rb') as f:
            image_data = f.read()
            base64_image = base64.b64encode(image_data).decode('utf-8')
        
        # Clean up temporary file
        os.unlink(tmp_file.name)
    
    return base64_image


def _execute_injection_code(code: str) -> str:
    """Execute the injection code and return base64 plot."""
    namespace = {
        'Class': Class,
        'plt': plt,
        'pi': pi,
        'tempfile': tempfile,
        'os': os,
        'base64': base64
    }
    
    exec(code, namespace)
    
    # Generate and return the plot
    return _save_plot_to_files()


def generate_llm_scientific_criteria(plot_description: str, plot_type: str = "scientific plot") -> str:
    """Generate domain-specific scientific criteria using LLM based on plot description."""
    try:
        from openai import OpenAI
        from .utils import get_api_keys_from_env
        
        api_keys = get_api_keys_from_env()
        client = OpenAI(api_key=api_keys["OPENAI"])
        
        prompt = f"""You are a scientific expert analyzing plots. Generate domain-specific scientific accuracy criteria for evaluating a {plot_type}.

Context: {plot_description}

Identify key features that should have specific expected coordinates/values (x-axis, y-axis positions, ratios, etc.). For each feature, specify:
1. Expected x/y coordinates or values
2. What deviations indicate and why they're scientifically invalid
3. What physical processes cause these features

IMPORTANT: Only include features you're confident about. Skip any where the expected values can vary significantly or you're uncertain. 
It's better to have fewer, more reliable criteria than many uncertain ones.

Example format:
"Feature name: Expected at x ≈ [value], y ≈ [value]
- If shifted to x < [value]: indicates [physical cause] (invalid because [reason])
- If shifted to x > [value]: indicates [physical cause] (invalid because [reason])
- If shifted to y < [value]: indicates [physical cause] (invalid because [reason])

Example (stellar main sequence):
"Main sequence turnoff: Expected at B-V ≈ 0.6, M_V ≈ 4.0 for solar metallicity
- If shifted bluer (B-V < 0.4): indicates higher metallicity/younger age (invalid for old globular clusters)
- If shifted redder (B-V > 0.8): indicates lower metallicity/older age (invalid for young open clusters)"

Provide similar specific criteria for this plot type, focusing only on features with well-defined expected values."""

        response = client.chat.completions.create(
            # TODO: make this a config variable
            # TODO: add tokens to cost tracking
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        print(f"ERROR: Failed to generate LLM scientific criteria: {e}")
        return ""


# TODO: check on None vs False here
def get_scientific_context(context_type: str = None, plot_description: str = None) -> str:
    """Get scientific context for domain-specific accuracy criteria."""
    if context_type is None:
        return ""
    
    if context_type == "llm_generated":
        if plot_description:
            return generate_llm_scientific_criteria(plot_description)
        else:
            return ""
    
    if context_type not in scientific_context:
        return ""
    
    return scientific_context[context_type]


def get_injection(config: dict[str, str] | bool | None = None, injection_name: str = None) -> Tuple[str, str]:
    """Get injection code and plot. Returns tuple of code to show and plot encoded as base64."""
    if config is None:
        config = injection_config
    
    if not config:
        raise ValueError("Injection is disabled (config is False)")
    if not isinstance(config, dict):
        raise ValueError("config must be a dict when enabled")
    
    config_dict: dict[str, str] = config  # At this point, config is guaranteed to be a dict
    
    if injection_name is None:
        if "plot_for_vlm" not in config_dict:
            raise ValueError(f"Missing required key 'plot_for_vlm' in injection config. Available keys: {list(config_dict.keys())}")
        injection_name = config_dict["plot_for_vlm"]
    
    if injection_name not in injection_plot_map:
        available = list(injection_plot_map.keys())
        raise ValueError(f"Injection '{injection_name}' not found. Available: {available}")
    
    # Get the plot and exact code
    direct_code, plot_base64 = injection_plot_map[injection_name]()
    
    # Choose what code to show based on config
    if "code_for_engineer" not in config_dict:
        raise ValueError(f"Missing required key 'code_for_engineer' in injection config. Available keys: {list(config_dict.keys())}")
    
    current_code_type: InjectionCode = config_dict["code_for_engineer"]
    
    if current_code_type == "template":
        code_to_show = cmb_template_code
    else:  # "exact"
        code_to_show = direct_code
    
    return code_to_show, plot_base64


# Scientific context for domain-specific accuracy criteria
scientific_context = {
    "cmb_power_spectra": """
For CMB TT power spectra analysis, the following scientific accuracy criteria must be met:

1. Main acoustic peak: Should be located near l=220 with D_l ≈ 5700 μK²
   - If first peak is shifted left (l < 200): implies closed universe (invalid for Planck 2018)
   - If first peak is shifted right (l > 240): implies open universe (invalid for Planck 2018)
   - Peak height is sensitive to primordial fluctuation amplitude and scalar spectral index

2. Second acoustic peak: Should be located near l=540 
   - Amplitude should be roughly 0.4 times the main peak amplitude
   - If peak is too high: indicates baryon density too low (invalid for Planck 2018)
   - If peak is too low: indicates baryon density too high (invalid for Planck 2018)

3. Third acoustic peak: Should be located near l=800
   - Height should be very similar to the second peak
   - If significantly higher: indicates higher proportion of dark matter (invalid for Planck 2018)

For a valid Planck 2018 TT power spectrum on a linear-linear plot, ALL of these criteria must be satisfied. 
Any deviation from these patterns indicates the plot does not represent valid Planck 2018 cosmological parameters.
"""
}


# Template code for CMB power spectrum (used when code_to_inject = "template")
cmb_template_code = """# Imports

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0    = ___,
    ombh2 = ___,
    omch2 = ___,
    mnu   = ___,  # Neutrino mass (minimal normal hierarchy)
    omk   = ___,
    tau   = ___,
    As    = ___,
    ns    = ___,
    lmax  = ___,
)

# Run CAMB to get results

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')  # Returns l(l + 1)C_l / 2 pi

# Construct multipole array

# Plot total (lensed) temperature power spectrum

# Label plot"""

def wrong_scalar_amplitude():
    direct_code = """import os
import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,              # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-10,          # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 0])

# Labels
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")
plt.grid(True)"""
    
    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def incorrect_units():
    direct_code = """import os
import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,              # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='K')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 0])

# Labels
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")"""
    
    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def truncated_multipole_range():
    """Missing title, using LaTeX, and truncated multipole moment range 2 ≤ l ≤ 1000"""
    direct_code = """import os
import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,              # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')  # raw_cl=False returns l(l + 1)C_l / 2 pi
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=300)
plt.plot(ls, totCL[:, 0])

# Labels
plt.xlim(2, 1000)
plt.xlabel(r'$ℓ$')
plt.ylabel(r'$ℓ(ℓ+1) C_ℓ^{TT} / 2π [μK^2]$')
plt.grid(True)"""
    
    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def wrong_scalar_spectral_index():
    direct_code = """import os
import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,              # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.5,                # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 0])

# Labels
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")
plt.grid(True)"""
    
    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def wrong_hubble_constant():
    direct_code = """import os
import camb
import numpy as np
import matplotlib.pyplot as plt

H0 = 100.0
h = H0 / 100.0

# Density fractions
Omega_b = 0.049
Omega_c = 0.26

# Convert to ombh2 and omch2
ombh2 = Omega_b * h**2
omch2 = Omega_c * h**2

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=H0,                 # Hubble constant (km/s/Mpc)
    ombh2=ombh2,           # Baryon density
    omch2=omch2,           # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 0])

# Labels
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")
plt.grid(True)"""
    
    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def no_rescaling():
    direct_code = """import os
import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,               # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=True, CMB_unit='muK')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 0])

# Labels
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")"""
    
    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def wrong_cmb_spectra():
    direct_code = """import os
import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,              # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 1])

# Labels
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")"""

    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def wrong_axis_scaling():
    direct_code = """import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,              # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 0])

# Labels
plt.yscale('log')
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")
plt.grid(True)"""

    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def overplotting_spectra():
    direct_code = """import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,              # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.0544,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 0])
plt.plot(ls, totCL[:, 1])

# Labels
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")
plt.grid(True)"""
    
    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


def wrong_optical_depth():
    direct_code = """import os
import camb
import numpy as np
import matplotlib.pyplot as plt

# Define cosmological parameters using Planck 2018
pars = camb.set_params(
    H0=67.36,              # Hubble constant (km/s/Mpc)
    ombh2=0.02237,         # Baryon density
    omch2=0.1200,          # Cold dark matter density
    mnu=0.06,              # Neutrino mass (minimal normal hierarchy)
    omk=0,                 # Flat universe
    tau=0.1044,            # Optical depth to reionization
    As=2.100e-9,           # Scalar amplitude
    ns=0.9649,             # Scalar spectral index
    lmax=2500
)

# Run CAMB to get results
results = camb.get_results(pars)

# Get the dictionary of CMB power spectra in μK^2
powers = results.get_cmb_power_spectra(pars, raw_cl=False, CMB_unit='muK')
totCL = powers['total']  # Includes lensing

# Multipole array
ls = np.arange(totCL.shape[0])

# Plot total (lensed) temperature power spectrum
plt.figure(dpi=200)
plt.plot(ls, totCL[:, 0])

# Labels
plt.xlim(2, 2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT [μK^2]')
plt.title("Lensed CMB TT Power Spectrum")
plt.grid(True)"""

    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


injection_plot_map = {
    "wrong_scalar_amplitude": wrong_scalar_amplitude,
    "truncated_multipole_range": truncated_multipole_range,
    "wrong_scalar_spectral_index": wrong_scalar_spectral_index,
    "wrong_hubble_constant": wrong_hubble_constant,
    "no_rescaling": no_rescaling,
    "incorrect_units": incorrect_units,
    "wrong_cmb_spectra": wrong_cmb_spectra,
    "wrong_axis_scaling": wrong_axis_scaling,
    "overplotting_spectra": overplotting_spectra,
    "wrong_optical_depth": wrong_optical_depth,
}