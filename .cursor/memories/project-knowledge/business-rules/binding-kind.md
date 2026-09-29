---
summary: "Optional binding kind stores the GUI action so Open app and Command stay distinct when they share an exec. Missing kind is inferred; the mapper ignores kind."
created: 2026-09-29
category: business-rules
domain: bindings
tags: [kind, command, app, config, gui]
related: [mskb_bindings.py, mskb_gui.py, docs/operator.md]
---

# Binding kind

`kind` is optional on a binding, same role as `label`: the window reads it, the mapper does not. Values are `app`, `key`, `command`, and `none`.

`binding_for_kind` always writes `kind` next to `key` and `exec`. App and command may share the same `exec` (`btop`) and still differ. Apply stays dirty-only; switching the action combo is a real change because the stored `kind` changed.

`kind_for_binding` prefers a known stored `kind`. If it is missing or invalid, infer from `key`/`exec`: exec wins (dual-fire loads as command). Pass `installed_execs` only for that fallback so a legacy command that matches an installed desktop app opens as Open app. Do not apply that heuristic when `kind` is already stored.

`as_binding` keeps a known `kind` (and a non-empty label) when the window loads config. Dropping `kind` there erases the choice on the next Apply.

Do not rewrite existing config on startup. A file without `kind` stays inferred until the user Applies that key.
