# Charts with matplotlib

Make the chart a PNG (or SVG), then place it in a document, deck or the outputs folder. Native office charts are fiddly; an image is reliable.

## Skeleton (write under outputs/ or work/)
```python
import matplotlib
matplotlib.use("Agg")                      # no display here: select the file-only backend before pyplot
import matplotlib.pyplot as plt

PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]  # colour-blind safe
plt.rcParams.update({"font.size": 12, "axes.prop_cycle": plt.cycler(color=PALETTE),
                     "axes.spines.top": False, "axes.spines.right": False})

fig, ax = plt.subplots(figsize=(8, 4.5))   # 16:9, fits a slide; use (6.5, 3.5) for a page
ax.bar(["Q1", "Q2", "Q3"], [120, 150, 180])
ax.set_title("Revenue by quarter"); ax.set_ylabel("Revenue ($k)")
ax.bar_label(ax.containers[0], fmt="%d")
fig.tight_layout()
fig.savefig("outputs/revenue.png", dpi=200)   # 200 dpi for documents and slides; .svg for scalable vector art
plt.close(fig)                                # always close: a loop of charts leaks memory
```

## Choosing
- Comparison across categories: bars (horizontal when labels are long). Trend over time: line. Share of a whole with 2 to 4 parts: bars or one stacked bar; avoid pies beyond that. Relationship of two numbers: scatter.
- Start bar charts at zero. Label axes with units. A title that states the finding beats one that names the variable.

## What bites
- Forgetting `matplotlib.use("Agg")` before `import pyplot` can crash on a machine with no display.
- `tight_layout()` (or `savefig(..., bbox_inches="tight")`) is what stops clipped labels. Rotate long tick labels: `plt.setp(ax.get_xticklabels(), rotation=30, ha="right")`.
- Use at most about 6 colours; do not rely on colour alone, vary line style or add direct labels and markers.
- Fonts under 10 pt become unreadable once the image is scaled into a slide or page.
- Dates: pass real `datetime` objects and let `matplotlib.dates` format the axis.
- Large point counts: downsample or use `alpha`; a million-point scatter is a blob and slow.
- Save one figure per file with a descriptive name; keep the source data next to it in work/ so it can be regenerated.
- Missing library: call `python_install` with `matplotlib`.

## Check your work (mandatory)
1. Assert the file exists and is not tiny (`os.path.getsize(path) > 5_000`); recompute the plotted numbers from the source data and compare with what you passed to the plot.
2. Call `render_preview` on the PNG (or `view_image` directly) and look: labels readable and not overlapping, legend not hiding data, axis ranges sensible, units present.
3. Fix and repeat at most twice.
