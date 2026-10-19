# Repo rules (binding for any agent working here)

1. Before creating, writing, or in any way producing ANY file — check first
   whether it already exists (ls / stat / read), in every scenario,
   including scratch and intermediate files. The check is a disconnect
   detector: a file existing where you expected none means your
   interpretation of the situation is probably wrong. Treat that as a
   stop-and-ask signal, reconcile with the request's wording (tense and
   phrasing often say whether something already happened), and never
   overwrite without explicit instruction. When in doubt, inspect the
   directory and ask.

2. Data files (*.las, *.laz, *.tif, *.npz) are never to be regenerated or
   overwritten. They are user-owned products.

3. Test input for this project: `sample.las` in the repo root (user-owned;
   see rule 2).
