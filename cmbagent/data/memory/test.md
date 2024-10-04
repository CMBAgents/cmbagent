# Plotting contours with getdist


To plot contours with getdist, use this strategy:

```python
from getdist import plots,loadMCSamples
import matplotlib.pyplot as plt

chains_list = []

path_to_chains = "/path/to/chainsA"
chains_list.append(path_to_chains)

path_to_chains = "/path/to/chainsB"
chains_list.append(path_to_chains)


all_samples = []
for i in range(len(chains_list)):
    readsamps = loadMCSamples(chains_list[i],settings={'ignore_rows':0.3})
    all_samples.append(readsamps)

g = plots.getSubplotPlotter()
g.settings.fig_width_inch = 10

g.settings.axes_fontsize = 10
g.settings.lab_fontsize =13

g.settings.legend_fontsize = 14
g.settings.alpha_filled_add=0.1
g.settings.colorbar_label_pad = 20.
g.settings.figure_legend_frame = False

g.settings.title_limit=0 #uncomment if you want to display marg stats.

g.triangle_plot(
    all_samples,
    ['omch2','ombh2','logA','ns','H0','sigma8','S825'],
    filled=[True,False],
    legend_labels=['chainsA','chainsB'],
    legend_loc='upper right',
    colors = ['blue','red'],
    line_args=[{'lw':'1','color':'blue'},{'lw':'2','color':'red'}])

plt.savefig('contour_plot.png')

```

Make sure you use `loadMCSamples`. 