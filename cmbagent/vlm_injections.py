import os
import base64
import tempfile
import matplotlib.pyplot as plt
from typing import Tuple, Literal
from classy import Class
from math import pi

# Valid plots to inject
InjectionPlot = Literal[
    "wrong_H0_value", 
    "truncated_multipole_range"
    ]

# Valid code context injections
InjectionCode = Literal["template", "exact"]

# Injection configuration (disabled by default)
injection_config: dict[str, str] | bool = False


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


# TODO: check on None vs False here
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
        injection_name = config_dict["plot"]
    if injection_name not in injection_plot_map:
        available = list(injection_plot_map.keys())
        raise ValueError(f"Injection '{injection_name}' not found. Available: {available}")
    
    # Get the plot and exact code
    direct_code, plot_base64 = injection_plot_map[injection_name]()
    
    # Choose what code to show based on config
    current_code_type: InjectionCode = config_dict["code"]
    if current_code_type == "template":
        code_to_show = cmb_template_code
    else:  # "exact"
        code_to_show = direct_code
    
    print(f"DEBUG: Using injection '{injection_name}' with code context '{current_code_type}'")
    
    return code_to_show, plot_base64


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
powers = results.get_cmb_power_spectra(pars, CMB_unit='muK')  # these are already dimensionless (D_ell)

# Construct multipole array

# Plot total (lensed) temperature power spectrum

# Label plot"""

def wrong_H0_value():
    """TODO: fix this"""
    direct_code = """from classy import Class
import matplotlib.pyplot as plt
from math import pi

# Create instance of the class "Class"
LambdaCDM = Class()

# Pass input parameters
LambdaCDM.set({'omega_b':0.0223828,'omega_cdm':0.1201075,'h':67.810,'A_s':2.100549e-09,'n_s':0.9660499,'tau_reio':0.05430842})
LambdaCDM.set({'output':'tCl,pCl,lCl,mPk','lensing':'yes','P_k_max_1/Mpc':3.0})

# Run class
LambdaCDM.compute()

# get all C_l output
cls = LambdaCDM.lensed_cl(2500)
ll = cls['ell'][2:]
clTT = cls['tt'][2:]

plt.xscale('linear')
plt.yscale('linear')
plt.xlim(2,2500)
plt.xlabel('l')
plt.ylabel('[l(l+1)/(2 pi)] C_l^TT')
plt.plot(ll,clTT*ll*(ll+1)/2./pi,'r-')
plt.title("LCDM CMB TT Power Spectrum")"""
    
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
powers = results.get_cmb_power_spectra(pars, CMB_unit='muK')
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


# Injection registry (plot generators)
injection_plot_map = {
    "wrong_H0_value": wrong_H0_value,
    "truncated_multipole_range": truncated_multipole_range,
}