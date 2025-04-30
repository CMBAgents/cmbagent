import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent


cmbagent = CMBAgent(agent_llm_configs = {
            # 'engineer': {
            #     "model": "gemini-2.5-pro-exp-03-25",
            #     "api_key": os.getenv("GEMINI_API_KEY"),
            #     "api_type": "google"
            # }

            'engineer': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "medium", # high
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                }
                })


task = r"""Run this code. Do NOT modify it. 

```python
# filename: codebase/oscillator_analysis.py
import numpy as np
import os
import datetime
from matplotlib import pyplot as plt
from matplotlib import rcParams
from sklearn.decomposition import PCA

# Enable LaTeX rendering and set font family for plots.
rcParams['text.usetex'] = True
rcParams['font.family'] = 'serif'


def create_dummy_simulation_data():
    dt = 0.01  # time step in seconds
    t = np.linspace(0, 10, int(10/dt) + 1)  # time vector from 0 to 10 seconds
    
    sim_results = {}
    
    # Coupling regime: kc_0.10 with f1 = 1 Hz, f2 = 2 Hz, phase shift = 0.0 rad.
    f1 = 1.0  # Hz for oscillator 1
    f2 = 2.0  # Hz for oscillator 2
    x1 = np.sin(2 * np.pi * f1 * t)
    x2 = np.sin(2 * np.pi * f2 * t)
    v1 = 2 * np.pi * f1 * np.cos(2 * np.pi * f1 * t)
    v2 = 2 * np.pi * f2 * np.cos(2 * np.pi * f2 * t)
    total_energy = x1**2 + x2**2
    sim_results['kc_0.10'] = {"t": t, "x1": x1, "x2": x2, "v1": v1, "v2": v2, "total_energy": total_energy}
    
    # Coupling regime: kc_1.00 with f1 = 1 Hz, f2 = 1.5 Hz, phase shift = 0.3 rad.
    f1 = 1.0  # Hz for oscillator 1
    f2 = 1.5  # Hz for oscillator 2
    phase = 0.3
    x1 = np.sin(2 * np.pi * f1 * t)
    x2 = np.sin(2 * np.pi * f2 * t + phase)
    v1 = 2 * np.pi * f1 * np.cos(2 * np.pi * f1 * t)
    v2 = 2 * np.pi * f2 * np.cos(2 * np.pi * f2 * t + phase)
    total_energy = x1**2 + x2**2
    sim_results['kc_1.00'] = {"t": t, "x1": x1, "x2": x2, "v1": v1, "v2": v2, "total_energy": total_energy}
    
    # Coupling regime: kc_10.00 with f1 = 0.5 Hz, f2 = 1.0 Hz, phase shift = -0.2 rad.
    f1 = 0.5  # Hz for oscillator 1
    f2 = 1.0  # Hz for oscillator 2
    phase = -0.2
    x1 = np.sin(2 * np.pi * f1 * t)
    x2 = np.sin(2 * np.pi * f2 * t + phase)
    v1 = 2 * np.pi * f1 * np.cos(2 * np.pi * f1 * t)
    v2 = 2 * np.pi * f2 * np.cos(2 * np.pi * f2 * t + phase)
    total_energy = x1**2 + x2**2
    sim_results['kc_10.00'] = {"t": t, "x1": x1, "x2": x2, "v1": v1, "v2": v2, "total_energy": total_energy}
    
    return sim_results


def perform_fourier_analysis(sim_data, dt, coupling_label, timestamp):
    t = sim_data["t"]
    x1 = sim_data["x1"]
    x2 = sim_data["x2"]
    n = len(t)
    freq = np.fft.rfftfreq(n, dt)
    X1_fft = np.fft.rfft(x1)
    X2_fft = np.fft.rfft(x2)
    amplitude1 = np.abs(X1_fft)
    amplitude2 = np.abs(X2_fft)
    # Ignore DC component (index 0) for peak detection.
    idx1 = np.argmax(amplitude1[1:]) + 1
    idx2 = np.argmax(amplitude2[1:]) + 1
    peak_freq_x1 = freq[idx1]
    peak_freq_x2 = freq[idx2]
    print(f"Fourier Analysis for {coupling_label}:")
    print(f"  Dominant peak frequency for Oscillator 1: {peak_freq_x1:.4f} Hz with amplitude {amplitude1[idx1]:.4f}")
    print(f"  Dominant peak frequency for Oscillator 2: {peak_freq_x2:.4f} Hz with amplitude {amplitude2[idx2]:.4f}")

    fig, axs = plt.subplots(2, 1, figsize=(8, 10))
    axs[0].plot(freq, amplitude1, color='b')
    axs[0].set_title(r'$
FFT\ ext{ of Oscillator 1 (}x_1\ ext{)}$')
    axs[0].set_xlabel(r'$
Frequency\ ext{ (Hz)}$')
    axs[0].set_ylabel(r'$
Amplitude$')
    axs[0].grid(True)
    axs[0].relim()
    axs[0].autoscale_view()

    axs[1].plot(freq, amplitude2, color='r')
    axs[1].set_title(r'$
FFT\ ext{ of Oscillator 2 (}x_2\ ext{)}$')
    axs[1].set_xlabel(r'$
Frequency\ ext{ (Hz)}$')
    axs[1].set_ylabel(r'$
Amplitude$')
    axs[1].grid(True)
    axs[1].relim()
    axs[1].autoscale_view()

    filename = f"data/spectrum_{coupling_label}_1_{timestamp}.png"
    fig.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved Fourier amplitude spectrum plot for {coupling_label} to {filename}\n")
    plt.close(fig)


def plot_energy_transfer(sim_data_dict, dt, timestamp):
    fig, ax = plt.subplots(figsize=(10, 6))
    for key, sim_data in sim_data_dict.items():
        t = sim_data["t"]
        total_energy = sim_data["total_energy"]
        dE_dt = np.gradient(total_energy, dt)
        mean_transfer_rate = np.mean(np.abs(dE_dt))
        print(f"Energy transfer rate for {key}: Mean |dE/dt| = {mean_transfer_rate:.4e} J/s")
        ax.plot(t, dE_dt, label=fr'$
\mathrm{{{key}\;:\;Mean\;|dE/dt|={mean_transfer_rate:.2e}\;J/s}}$')
    
    ax.set_title(r'$
Energy\;Transfer\;Rate\;(dE/dt)\;vs.\;Time$')
    ax.set_xlabel(r'$
Time\;(s)$')
    ax.set_ylabel(r'$
\frac{dE}{dt}\;(J/s)$')
    ax.grid(True)
    ax.relim()
    ax.autoscale_view()
    ax.legend()
    
    filename = f"data/energy_transfer_1_{timestamp}.png"
    fig.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved energy transfer rate plot to {filename}\n")
    plt.close(fig)


def plot_total_energy_comparison(sim_data_dict, timestamp):
    # Selected regimes: weak (kc_0.10), critical (kc_1.00), strong (kc_10.00).
    selected_keys = [key for key in sim_data_dict.keys() if key in ['kc_0.10', 'kc_1.00', 'kc_10.00']]
    fig, ax = plt.subplots(figsize=(10, 6))
    for key in selected_keys:
        sim_data = sim_data_dict[key]
        t = sim_data["t"]
        total_energy = sim_data["total_energy"]
        ax.plot(t, total_energy, label=fr'$
\mathrm{{{key}}}$')
        print(f"Comparative Energy Plot for {key}: Initial Total Energy = {total_energy[0]:.4f} J, Final Total Energy = {total_energy[-1]:.4f} J")
    
    ax.set_title(r'$
Total\;Energy\;Comparison\;for\;Selected\;Coupling\;Regimes$')
    ax.set_xlabel(r'$
Time\;(s)$')
    ax.set_ylabel(r'$
Total\;Energy\;(J)$')
    ax.grid(True)
    ax.relim()
    ax.autoscale_view()
    ax.legend()
    
    filename = f"data/total_energy_comparison_2_{timestamp}.png"
    fig.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved total energy comparison plot to {filename}\n")
    plt.close(fig)


def plot_phase_portraits(sim_data, timestamp):
    x1 = sim_data["x1"]
    v1 = sim_data["v1"]
    x2 = sim_data["x2"]
    v2 = sim_data["v2"]
    
    fig, axs = plt.subplots(1, 2, figsize=(12, 6))
    axs[0].plot(x1, v1, 'b-', lw=0.5)
    axs[0].set_title(r'$
Phase\;Portrait\;of\;Oscillator\;1$')
    axs[0].set_xlabel(r'$
\x_1\;(m)$')
    axs[0].set_ylabel(r'$
\v_1\;(m/s)$')
    axs[0].grid(True)
    axs[0].relim()
    axs[0].autoscale_view()
    
    axs[1].plot(x2, v2, 'r-', lw=0.5)
    axs[1].set_title(r'$
Phase\;Portrait\;of\;Oscillator\;2$')
    axs[1].set_xlabel(r'$
\x_2\;(m)$')
    axs[1].set_ylabel(r'$
\v_2\;(m/s)$')
    axs[1].grid(True)
    axs[1].relim()
    axs[1].autoscale_view()
    
    filename = f"data/phase_portraits_3_{timestamp}.png"
    fig.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved phase portraits plot to {filename}\n")
    plt.close(fig)


def plot_poincare_section(sim_data, timestamp):
    x1 = sim_data["x1"]
    v1 = sim_data["v1"]
    x2 = sim_data["x2"]
    v2 = sim_data["v2"]
    t = sim_data["t"]
    
    crossings = []
    for i in range(1, len(x1)):
        if x1[i-1] < 0 and x1[i] >= 0:
            # Linear interpolation for more accurate crossing time.
            frac = -x1[i-1] / (x1[i] - x1[i-1])
            t_cross = t[i-1] + frac * (t[i] - t[i-1])
            x2_cross = x2[i-1] + frac * (x2[i] - x2[i-1])
            v2_cross = v2[i-1] + frac * (v2[i] - v2[i-1])
            crossings.append((x2_cross, v2_cross))
    crossings = np.array(crossings)
    
    if crossings.size == 0:
        print("No zero crossings found for Poincaré section computation.\n")
        return
    
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(crossings[:, 0], crossings[:, 1], s=10, color='m')
    ax.set_title(r'$
Poincar\acute{e}\;Section\;(Oscillator\;2)\;at\;x_1=0$')
    ax.set_xlabel(r'$
\x_2\;(m)$')
    ax.set_ylabel(r'$
\v_2\;(m/s)$')
    ax.grid(True)
    ax.relim()
    ax.autoscale_view()
    
    filename = f"data/poincare_section_4_{timestamp}.png"
    fig.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved Poincaré section plot to {filename}\n")
    plt.close(fig)


def perform_pca(sim_data, timestamp):
    x1 = sim_data["x1"]
    v1 = sim_data["v1"]
    x2 = sim_data["x2"]
    v2 = sim_data["v2"]
    state_matrix = np.column_stack((x1, v1, x2, v2))
    pca = PCA(n_components=4)
    pca.fit(state_matrix)
    explained_variance = pca.explained_variance_ratio_
    print("PCA Explained Variance Ratios:")
    for i, var in enumerate(explained_variance):
        print(f"  Component {i+1}: {var:.4f}")
    
    projection = pca.transform(state_matrix)
    fig, ax = plt.subplots(figsize=(8, 6))
    sc = ax.scatter(projection[:, 0], projection[:, 1], s=1, c=np.linspace(0, 1, projection.shape[0]), cmap='viridis')
    ax.set_title(r'$
PCA\;Projection\;onto\;First\;Two\;Components$')
    ax.set_xlabel(r'$
\PC_1$')
    ax.set_ylabel(r'$
\PC_2$')
    ax.grid(True)
    ax.relim()
    ax.autoscale_view()
    
    filename = f"data/PCA_projection_5_{timestamp}.png"
    fig.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved PCA projection plot to {filename}\n")
    plt.close(fig)
    
    fig, ax = plt.subplots(figsize=(8, 6))
    components = np.arange(1, len(explained_variance) + 1)
    ax.bar(components, explained_variance, color='c')
    ax.set_title(r'$
Explained\;Variance\;Ratio\;per\;Component$')
    ax.set_xlabel(r'$
Principal\;Component$')
    ax.set_ylabel(r'$
Explained\;Variance\;Ratio$')
    ax.set_xticks(components)
    ax.grid(True)
    ax.relim()
    ax.autoscale_view()
    
    filename = f"data/PCA_variance_6_{timestamp}.png"
    fig.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Saved PCA variance ratio plot to {filename}\n")
    plt.close(fig)


if __name__ == "__main__":
    # Ensure the data directory exists
    os.makedirs("data", exist_ok=True)
    
    data_file = "data/coupled_oscillators_data.npz"
    # If the data file doesn't exist, create dummy simulation data and save it.
    if not os.path.exists(data_file):
        print(f"Data file {data_file} not found. Creating dummy simulation data.")
        sim_results = create_dummy_simulation_data()
        np.savez(data_file, results=sim_results)
        print(f"Dummy simulation data saved to {data_file}\n")
    else:
        try:
            npz_data = np.load(data_file, allow_pickle=True)
            sim_results = npz_data["results"].item()
            print(f"Loaded simulation data from {data_file}\n")
        except Exception as e:
            print(f"Error loading data from {data_file}: {e}")
            exit(1)
            
    dt = 0.01  # Time step in seconds.
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    
    # Fourier Analysis for each simulation.
    print("Performing Fourier Analysis on each simulation:")
    for key, sim_data in sim_results.items():
        perform_fourier_analysis(sim_data, dt, key, timestamp)
    
    # Energy Transfer Analysis.
    print("Performing Energy Transfer Analysis:")
    plot_energy_transfer(sim_results, dt, timestamp)
    
    # Comparative Total Energy Plot.
    print("Generating Comparative Total Energy Plot:")
    plot_total_energy_comparison(sim_results, timestamp)
    
    # Phase Portraits and Poincaré Section for simulation with kc_1.00.
    if 'kc_1.00' in sim_results:
        print("Generating Phase Portraits for kc_1.00:")
        plot_phase_portraits(sim_results['kc_1.00'], timestamp)
        print("Generating Poincaré Section for kc_1.00:")
        plot_poincare_section(sim_results['kc_1.00'], timestamp)
    else:
        print("Simulation data for kc_1.00 not found; skipping phase portraits and Poincaré section.")
    
    # PCA Analysis for simulation with kc_1.00.
    if 'kc_1.00' in sim_results:
        print("Performing PCA Analysis on kc_1.00 simulation:")
        perform_pca(sim_results['kc_1.00'], timestamp)
    else:
        print("Simulation data for kc_1.00 not found; skipping PCA analysis.")
    
    print("Data analysis complete. All plots and key statistics have been saved and printed.")
```
"""

cmbagent.solve(task,
               max_rounds=50,
               initial_agent='engineer',
               mode = "one_shot",
              )