import os
import base64
import tempfile
import matplotlib.pyplot as plt
from typing import Tuple, Literal
from math import pi

# Default plot type for VLM injection
vlm_injection_plot_type: str = "wrong_scalar_amplitude"


def _save_plot_to_files() -> str:
    """Save injected plot to temp file and debug location. All plots named the same."""
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
        plt.savefig(tmp_file.name, dpi=300, bbox_inches='tight')
        
        # Also save a copy to synthetic_output for debugging
        try:
            # Use relative path from current working directory
            synthetic_dir = os.path.join(os.getcwd(), "synthetic_output")
            os.makedirs(synthetic_dir, exist_ok=True)
            debug_path = os.path.join(synthetic_dir, "injected_plot.png")
            plt.savefig(debug_path, dpi=300, bbox_inches='tight')
            print(f"Injected plot saved to {debug_path} for reference")
        except Exception as e:
            print(f"Could not save injected plot to synthetic_output: {e}")
        
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


def get_injection_by_name(injection_name: str, code_template: str = "exact") -> Tuple[str, str]:
    """Get injection code and plot by name and template type. Returns tuple of code to show and plot encoded as base64."""
    if injection_name not in injection_plot_map:
        available = list(injection_plot_map.keys())
        raise ValueError(f"Injection '{injection_name}' not found. Available: {available}")
    
    # Get the plot and exact code
    direct_code, plot_base64 = injection_plot_map[injection_name]()
    
    # Choose what code to show based on template type
    if code_template == "exact":
        code_to_show = direct_code
    elif code_template == "cmb_power_spectra_template":
        code_to_show = template_codes["cmb"]
    elif code_template == "mean_reversion_trading_template":
        code_to_show = template_codes["trading"]
    else:
        raise ValueError(f"Unknown code template: {code_template}")
    
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


# Template codes for different plot types
template_codes = {
    "cmb": """# Imports

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

# Label plot""",
    
    "trading": """# Imports

# Download historical price data for PLTR from Yahoo finance, using close as reference price
df = yf.download("PLTR", start="2022-01-01", end="2024-01-01")
df['Price'] = df['Close']

# Compute 20-day rolling mean and standard deviation bands (±1σ)

# Calculate Z-score: (Price - Mean) / Std

# Handle NaNs appropriately
df.dropna(inplace=True)

# Generate buy/sell signals

# Compute standard deviation bands

# Plot price, rolling mean, and standard deviation bands.

# Overlay buy and sell signals (s=80 for decluttering)

# Formatting (legend, title, etc.)"""
}


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


def wrong_signal_colors():
    direct_code = """# Imports
import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import yfinance as yf

# Download historical price data for PLTR from Yahoo finance, using close as reference price
df = yf.download("PLTR", start="2022-01-01", end="2024-01-01")
df['Price'] = df['Close']

# Compute 20-day rolling mean and standard deviation bands (±1σ)
window = 20
df['RollingMean'] = df['Price'].rolling(window=window).mean()
df['RollingStd'] = df['Price'].rolling(window=window).std()

# Calculate Z-score: (Price - Mean) / Std
df['ZScore'] = (df['Price'] - df['RollingMean']) / df['RollingStd']

# Add the new columns and handle NaNs appropriately
df.dropna(inplace=True)

# Generate buy/sell signals (Z-score threshold = ±1)
df['Signal'] = 0
df.loc[df['ZScore'] < -1, 'Signal'] = 1   # Long
df.loc[df['ZScore'] > 1, 'Signal'] = -1  # Short

# Compute standard deviation bands
x = df.index
y1 = df['RollingMean'] + df['RollingStd']
y2 = df['RollingMean'] - df['RollingStd']

# Plot price, rolling mean, and standard deviation bands.
plt.figure(figsize=(14, 7), dpi=150)
plt.plot(df.index, df['Price'], label='PLTR Price', color='black')
plt.plot(df.index, df['RollingMean'], label='20-Day Rolling Mean', linestyle='--', color='gray')
plt.fill_between(x, y1, y2, color='gray', alpha=0.2, label='±1 Std Dev')

# Overlay buy (green upward triangle) and sell (red downward triangle) signals (s=80 for decluttering)
buy_signals = df[df['Signal'] == 1]
sell_signals = df[df['Signal'] == -1]
plt.scatter(buy_signals.index, buy_signals['Price'], marker='^', color='red', label='Buy Signal', s=80)
plt.scatter(sell_signals.index, sell_signals['Price'], marker='v', color='green', label='Sell Signal', s=80)

# Formatting (legend, title, etc.)
plt.title("Mean Reversion Signals for PLTR")
plt.ylabel("Price")
plt.xlabel("Date")
plt.grid(True)
plt.legend()
plt.tight_layout()"""

    plot_base64 = _execute_injection_code(direct_code)
    return direct_code, plot_base64


injection_plot_map = {
    # CMB power spectra
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
    # Trading signals
    "wrong_signal_colors": wrong_signal_colors,
}