# (trimmed to the atom-count baseline section for the test fixture)

- `gap` is exactly `lumo` minus `homo`, with a worst-case residual of 1e-4. It is an identity, not an empirical relationship. Any covariance, graph or decomposition method will see a rank-deficient trio, and rediscovering this relation is rediscovering a definition rather than finding anything.
- `u0`, `u298`, `h298` and `g298` are pairwise correlated at r = +1.000000 to six decimal places. For any multivariate purpose these are one variable, not four.
- `u0_atom`, `u298_atom`, `h298_atom` and `g298_atom` are pairwise correlated at r >= +0.99980. Likewise one variable.
- `B` and `C` correlate at +0.981, but `A` and `B` correlate at +0.0006. The three rotational constants are not a single block.

Seven of the nineteen numeric columns are therefore near-restatements of other columns, and they manufacture apparent low-dimensional structure. Principal components on the standardised numeric columns:

| columns used | p | PC1 | PC1-2 | components for 90% | for 99% |
|---|---|---|---|---|---|
| all numeric | 19 | 41.0% | 63.0% | 6 | 9 |
| dropping u298, h298, g298, u298_atom, h298_atom, g298_atom, gap | 12 | 35.7% | 54.9% | 7 | 10 |

Note the direction of that change. Removing the redundant columns *raises* the effective dimensionality, from six components to seven at the 90% level and from nine to ten at 99%. The dataset looks more tightly organised than it is, purely because several columns restate others. Any analysis reporting low-dimensional joint structure should establish that it survives the removal of these seven columns, and should report the twelve-column result alongside its own; otherwise a headline such as "two components explain 63% of the variance" is a fact about the column list rather than about chemistry.

The genuinely joint structure is elsewhere, and it is real. The atomisation energies are not a restatement of the total energies: `u0_atom` against `u0` is r = -0.237. Yet `u0_atom` tracks `zpve` at -0.967, `alpha` at -0.833 and `cv` at -0.767. That is a set of four properties bound tightly together across three of the four conventional categories, with nothing in their definitions requiring it. Structure of that kind — found, explained, and shown to reorganise across families of molecules — is what this project is for.

## The atom-count baseline

**Several of the columns are almost completely determined by atom counts alone.** A five-feature linear regression on nothing but the counts of C, N, O, F and H atoms achieves the following R-squared values, measured on this file:

  | column | R-squared from atom counts alone |
  |---|---|
  | u0, u298, h298, g298 | 0.9999 |
  | zpve | 0.9971 |
  | u298_atom | 0.9890 |
  | u0_atom | 0.9887 |
  | alpha | 0.8628 |
  | cv | 0.6999 |
  | lumo | 0.4514 |
